"""設定から、使うモデルのエンジンを組み立てる(ADR-0033 2章・3章)。

- ローカルの ONNX は、モデルごとにエンジンを1つだけ持つ(セッションを使い回す)。別のモデルに
  切り替えたら、前のエンジンのセッションを手放す。
- リモートは、呼ぶたびに接続先(Base URL とキー)を引き直す(画面で変えたらすぐ効くように)。
- `FAKE_PROVIDER=1` では、どのモデルでもダミーのエンジン(`FakeEmbeddingEngine`)を使う。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.orm import Session

from app.domain import embedding_settings, llm_connections
from app.embedding.base import EmbeddingEngine, EmbeddingError
from app.embedding.catalog import CLIP_MODELS
from app.embedding.fake import FakeEmbeddingEngine
from app.embedding.onnx_engine import OnnxClipEngine
from app.embedding.remote import InfinityEngine
from app.i18n import t
from app.model_store.onnx_runtime import available_memory_bytes
from app.model_store.residency import ModelResidency

if TYPE_CHECKING:
    from app.config import Settings


class EmbeddingEngines:
    def __init__(
        self,
        settings: Settings,
        *,
        memory_probe: Callable[[], int | None] = available_memory_bytes,
        residency: ModelResidency | None = None,
        remote_client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.settings = settings
        self._memory_probe = memory_probe
        self._residency = residency
        self._remote_client_factory = remote_client_factory
        self._lock = threading.Lock()
        self._onnx: OnnxClipEngine | None = None
        self._fakes: dict[str, FakeEmbeddingEngine] = {}

    def engine_for(
        self, db: Session, config: embedding_settings.EmbeddingConfig
    ) -> EmbeddingEngine:
        """使うモデルのエンジン。使えなければ EmbeddingError。"""
        model_key = embedding_settings.active_model_key(config, self.settings)
        if model_key is None:
            raise EmbeddingError(t("embeddings.notConfigured"))
        if self.settings.fake_provider:
            return self._fake(model_key, config)
        if config.engine == "onnx":
            return self._onnx_engine(config.onnx_model)
        return self._remote(db, config)

    def _fake(
        self, model_key: str, config: embedding_settings.EmbeddingConfig
    ) -> FakeEmbeddingEngine:
        with self._lock:
            engine = self._fakes.get(model_key)
            if engine is None:
                batch = 8
                if config.engine == "onnx" and config.onnx_model in CLIP_MODELS:
                    batch = CLIP_MODELS[config.onnx_model].image_batch_size
                engine = FakeEmbeddingEngine(model_key, image_batch_size=batch)
                self._fakes[model_key] = engine
            return engine

    def _onnx_engine(self, name: str) -> OnnxClipEngine:
        model = CLIP_MODELS[name]
        with self._lock:
            current = self._onnx
            if current is not None and current.model.name == name:
                return current
            if current is not None:
                current.close()
            engine = OnnxClipEngine(
                self.settings.data_dir,
                model,
                memory_probe=self._memory_probe,
                residency=self._residency,
            )
            self._onnx = engine
            return engine

    def _remote(self, db: Session, config: embedding_settings.EmbeddingConfig) -> InfinityEngine:
        assert config.remote_connection_id is not None and config.remote_model is not None
        connection = llm_connections.find_connection(
            llm_connections.load_user_connections(db), config.remote_connection_id
        )
        if connection is None or connection.base_url is None:
            raise EmbeddingError(t("embeddings.connectionMissing"))
        # キーが無ければ送らない(ダミーのキーも送らない)。
        api_key = llm_connections.read_connection_key(self.settings.data_dir, connection.id)
        return InfinityEngine(
            connection_id=connection.id,
            base_url=connection.base_url,
            model=config.remote_model,
            api_key=api_key,
            client_factory=self._remote_client_factory,
        )

    def release_idle(self) -> None:
        with self._lock:
            engine = self._onnx
        if engine is not None:
            engine.release_idle()

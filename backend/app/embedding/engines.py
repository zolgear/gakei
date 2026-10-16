"""設定から、使うモデルのエンジンを組み立てる(ADR-0033 2章・3章)。

- ローカルの ONNX は、モデルごとにエンジンを1つだけ持つ(セッションを使い回す)。別のモデルに
  切り替えたら、前のエンジンのセッションを手放す。
- リモートは、呼ぶたびに接続先(Base URL とキー)を引き直す(画面で変えたらすぐ効くように)。
- `FAKE_PROVIDER=1` では、どのモデルでもダミーのエンジン(`FakeEmbeddingEngine`)を使う。
- リモートのエンジンに送る前に、Mod の `ai.before_send`(目的 `embedding`。ADR-0051 2章)を呼ぶ
  (`before_send`)。ローカルの ONNX とダミーのエンジンは外へ送らないので呼ばない。
"""

from __future__ import annotations

import threading
import uuid
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
from app.mods import events as mods_events
from app.mods.api import AI_BEFORE_SEND, Actor, BeforeSendSubject, ImageRef, TextPart

if TYPE_CHECKING:
    from app.config import Settings
    from app.mods.runtime import Denial, ModRuntime


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
        # ADR-0051: 送る前のチェック。lifespan で入れる(無ければ呼ばない)。
        self.mods: ModRuntime | None = None

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
                max_text_tokens: int | None = None
                document_prefix = ""
                if config.engine == "onnx" and config.onnx_model in CLIP_MODELS:
                    model = CLIP_MODELS[config.onnx_model]
                    batch = model.image_batch_size
                    max_text_tokens = model.max_text_tokens
                    document_prefix = model.document_prefix
                engine = FakeEmbeddingEngine(
                    model_key,
                    image_batch_size=batch,
                    max_text_tokens=max_text_tokens,
                    document_prefix=document_prefix,
                )
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

    def before_send(
        self,
        engine: EmbeddingEngine,
        *,
        text: list[TextPart] | None = None,
        images: list[ImageRef] | None = None,
        actor: Actor | None = None,
        actor_user_id: uuid.UUID | None = None,
        extra_images: dict[str, bytes] | None = None,
    ) -> Denial | None:
        """リモートのエンジンに送る直前の `ai.before_send`(同期。スレッドから呼ぶ)。止めたら
        Denial。リモートでないエンジン・有効な購読が無いときは何もしない(DB にも触れない)。"""
        mods = self.mods
        if (
            mods is None
            or not isinstance(engine, InfinityEngine)
            or not mods.wants(AI_BEFORE_SEND, purpose="embedding")
        ):
            return None
        with mods.session_factory() as db:
            connection = llm_connections.find_connection(
                llm_connections.load_user_connections(db), engine.connection_id
            ) or llm_connections.ConnectionConfig(
                id=engine.connection_id, name="", base_url=engine.base_url
            )
            target = mods_events.llm_target(
                db,
                self.settings,
                connection,
                model=engine.model,
                base_url=engine.base_url,
                provider="infinity",
            )
            if actor is None and actor_user_id is not None:
                actor = mods_events.actor_for_user_id(db, actor_user_id)
        subject = BeforeSendSubject(
            purpose="embedding",
            target=target,
            text=list(text or []),
            params={"model": engine.model, "modality": "image" if images else "text"},
            images=list(images or []),
        )
        return mods.before_send_blocking(subject, actor=actor, extra_images=extra_images)

    def release_idle(self) -> None:
        with self._lock:
            engine = self._onnx
        if engine is not None:
            engine.release_idle()

"""FastAPI の Depends 用アクセサ。`app.state` に置いたオブジェクトを取り出すだけ。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from app.annotation.wd_models import WdModelDownloader
from app.config import Settings
from app.domain.storage import AssetStore
from app.embedding.catalog import ClipModelDownloader
from app.providers.base import ImageProvider
from app.providers.registry import ProviderRegistry
from app.worker.annotator import Annotator
from app.worker.embedder import Embedder
from app.worker.progress import ProgressBus
from app.worker.runner import Runner


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_session_factory(request: Request) -> sessionmaker:
    """ADR-0013: 接続する ComfyUIProvider を組み立てるときなど、Session ではなく
    session_factory そのものが要る箇所向け。
    """
    return request.app.state.session_factory


def get_store(request: Request) -> AssetStore:
    return request.app.state.store


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def get_provider(request: Request) -> ImageProvider:
    """主プロバイダー(OpenAI。FAKE_PROVIDER=1 のときは fake)を返す。`/api/settings/openai-key`
    が使う(ADR-0017)。"""
    return request.app.state.registry.primary_provider


def get_progress_bus(request: Request) -> ProgressBus:
    return request.app.state.progress_bus


def get_runner(request: Request) -> Runner:
    return request.app.state.runner


def get_data_dir(request: Request) -> Path:
    return request.app.state.settings.data_dir


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_frontend_dist(request: Request) -> Path:
    """`frontend/dist`(ビルド成果物)のパス。ADR-0021 の第三者ライセンス表記が読む。

    `app/main.py::create_app` が設定する(`_FRONTEND_DIST` から解決した実際の値。
    `tests/conftest.py` の差し替えもここに反映される)。
    """
    return request.app.state.frontend_dist


def get_annotator(request: Request) -> Annotator:
    """ADR-0024: 自動タイトル・タグの推定の worker。"""
    return request.app.state.annotator


def get_wd_downloader(request: Request) -> WdModelDownloader:
    """ADR-0024: ONNX タガーのモデルのダウンロード。"""
    return request.app.state.wd_downloader


def get_embedder(request: Request) -> Embedder:
    """ADR-0033: 画像の埋め込みの worker。"""
    return request.app.state.embedder


def get_clip_downloader(request: Request) -> ClipModelDownloader:
    """ADR-0033: 埋め込みのモデルのダウンロード。"""
    return request.app.state.clip_downloader


def get_embedding_index_backend(request: Request) -> str:
    """ADR-0033 4章: 検索の方式(`pgvector` / `numpy`)。起動時に決める。"""
    return request.app.state.embedding_index_backend

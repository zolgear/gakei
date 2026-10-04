"""GET /api/capabilities。複数プロバイダーの一覧を返す(ADR-0013)。

フロントはこれだけで、プロバイダーごとのフォームを組み立てる。`capabilities()` と
`availability()` はリクエストのたびに呼ぶ(キャッシュは各プロバイダーの責任)。

画像の埋め込み(ADR-0033 7章)が使えるかと、使うモデルの対応言語も返す(検索などの入口の
出し分けに使う)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import get_embedding_index_backend, get_registry, get_session, get_settings
from app.domain import embedding_settings
from app.domain.schemas import CapabilitiesResponse, EmbeddingCapabilities, ProviderEntry
from app.domain.semantic_search import active_languages
from app.providers.registry import ProviderRegistry

router = APIRouter(tags=["capabilities"])


def embedding_capabilities(
    db: Session, settings: Settings, index_backend: str
) -> EmbeddingCapabilities:
    config = embedding_settings.load(db)
    available = embedding_settings.usable(config, settings)
    languages = active_languages(config) if available else None
    return EmbeddingCapabilities(
        available=available,
        model_key=embedding_settings.active_model_key(config, settings) if available else None,
        languages=list(languages) if languages is not None else None,  # type: ignore[arg-type]
        multilingual=("ja" in languages) if languages is not None else None,
        index_backend=index_backend,  # type: ignore[arg-type]
        engine=config.engine if available else None,
    )


@router.get(
    "/api/capabilities", response_model=CapabilitiesResponse, operation_id="get_capabilities"
)
def get_capabilities_endpoint(
    registry: ProviderRegistry = Depends(get_registry),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    index_backend: str = Depends(get_embedding_index_backend),
) -> CapabilitiesResponse:
    entries: list[ProviderEntry] = []
    for provider in registry.providers.values():
        caps = provider.capabilities()
        available, unavailable_reason = provider.availability()
        entries.append(
            ProviderEntry(
                **caps.model_dump(),
                available=available,
                unavailable_reason=unavailable_reason,
                requires_api_key=provider.requires_api_key,
                supports_pricing=provider.supports_pricing,
            )
        )
    return CapabilitiesResponse(
        default_provider=registry.primary,
        providers=entries,
        embeddings=embedding_capabilities(db, settings, index_backend),
    )

"""GET /api/capabilities。複数プロバイダーの一覧を返す(ADR-0013)。

フロントはこれだけで、プロバイダーごとのフォームを組み立てる。`capabilities()` と
`availability()` はリクエストのたびに呼ぶ(キャッシュは各プロバイダーの責任)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_registry
from app.domain.schemas import CapabilitiesResponse, ProviderEntry
from app.providers.registry import ProviderRegistry

router = APIRouter(tags=["capabilities"])


@router.get(
    "/api/capabilities", response_model=CapabilitiesResponse, operation_id="get_capabilities"
)
def get_capabilities_endpoint(
    registry: ProviderRegistry = Depends(get_registry),
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
    return CapabilitiesResponse(default_provider=registry.primary, providers=entries)

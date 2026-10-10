"""複数プロバイダーの登録簿(ADR-0013)。主プロバイダー(OpenAI。`FAKE_PROVIDER=1` のときは
fake。ADR-0017)を必ず登録する。"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.providers import get_provider
from app.providers.base import ImageProvider

logger = logging.getLogger(__name__)


@dataclass
class ProviderRegistry:
    providers: dict[str, ImageProvider]
    primary: str

    def get(self, name: str) -> ImageProvider | None:
        return self.providers.get(name)

    @property
    def primary_provider(self) -> ImageProvider:
        return self.providers[self.primary]

    def set_provider(self, name: str, provider: ImageProvider | None) -> None:
        """ADR-0013 7章、ADR-0038 6章: 設定画面からの接続・切り離しを、再起動なしで反映する。

        `provider=None` は切り離し(capabilities から消える。ComfyUI の登録済みワークフローと
        過去の Run は消さない)。主プロバイダーは切り離さない前提で、呼び出し側は
        `comfyui` / `sdwebui` だけを渡す。
        """
        if provider is None:
            self.providers.pop(name, None)
        else:
            self.providers[name] = provider


def is_loopback_url(url: str) -> bool:
    """ホスト部が 127.0.0.0/8、::1、localhost のいずれかどうか。"""
    host = urlparse(url).hostname
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_registry(settings: Settings, session_factory: sessionmaker) -> ProviderRegistry:
    """設定からプロバイダーの登録簿を組み立てる。

    `session_factory` は、DB に登録したワークフローを読む ComfyUI プロバイダーに渡す。

    ComfyUI の接続先は起動時に一度だけ解決する(ADR-0013 7章)。DB の設定
    (`app_setting`。画面での接続・切り離し)があればそれを、無ければ環境変数
    `COMFYUI_URL` を既定値として使う。以降の接続・切り離しは `ProviderRegistry.set_provider`
    で再起動なしに反映する(`app/api/comfyui.py`)。
    """
    from app.domain.comfyui_connection import resolve_effective_url

    # ADR-0017: 主プロバイダーは常に openai。FAKE_PROVIDER=1 のときだけ fake に切り替える
    # (開発・CI・確認用)。
    primary_name = "fake" if settings.fake_provider else "openai"
    providers: dict[str, ImageProvider] = {
        primary_name: get_provider(primary_name, moderation=settings.moderation)
    }

    with session_factory() as session:
        comfyui_url, _source = resolve_effective_url(session, settings)

    if comfyui_url:
        if not is_loopback_url(comfyui_url):
            logger.warning(
                "ComfyUI の接続先がループバック以外を指しています(%s)。"
                "認証のない ComfyUI に入力画像とプロンプトを送信することになります。",
                comfyui_url,
            )

        from app.providers.comfyui.provider import ComfyUIProvider

        providers["comfyui"] = ComfyUIProvider(
            comfyui_url, session_factory, settings.comfyui_timeout_seconds
        )

    sdwebui = build_sdwebui_provider(settings, session_factory)
    if sdwebui is not None:
        providers["sdwebui"] = sdwebui

    return ProviderRegistry(providers=providers, primary=primary_name)


def build_sdwebui_provider(
    settings: Settings, session_factory: sessionmaker
) -> ImageProvider | None:
    """SD WebUI の接続先(ADR-0038 6章。保存値 > `SDWEBUI_URL` > 無効)を起動時に解決し、
    有効ならプロバイダーを作る。以降の接続・切り離しは `app/api/sdwebui.py` が
    `ProviderRegistry.set_provider` で反映する。"""
    from app.domain.sdwebui_connection import resolve_effective_url as resolve_sdwebui_url

    with session_factory() as session:
        url, _source = resolve_sdwebui_url(session, settings)
    if not url:
        return None
    if not is_loopback_url(url):
        logger.warning(
            "SD WebUI の接続先がループバック以外を指しています(%s)。"
            "入力画像とプロンプトをネットワーク越しに送信することになります。",
            url,
        )

    from app.providers.sdwebui.provider import SdWebuiProvider

    return SdWebuiProvider.from_settings(url, settings, session_factory)

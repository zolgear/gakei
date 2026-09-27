"""プロバイダーの組み立て。"""

from __future__ import annotations

from app.i18n import t
from app.providers.base import ImageProvider


def get_provider(name: str, *, moderation: str = "low") -> ImageProvider:
    """名前からプロバイダーを1つ組み立てる。

    `openai` はキーが無くても生成できる(ADR-0012: キーの解決は実行時まで遅延する)。
    `moderation` は generate のときだけ、コンストラクタで受け取って `finalize_params` が
    params に足す(ADR-0003: params は API に送った値そのもの)。未知の `name` は
    分かりやすく失敗する。
    """
    if name == "fake":
        from app.providers.fake import FakeProvider

        return FakeProvider(moderation=moderation)
    if name == "openai":
        from app.providers.openai_images import OpenAIImagesProvider

        return OpenAIImagesProvider(moderation=moderation)
    raise ValueError(t("provider.unknown", name=name))

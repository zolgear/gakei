"""画像バイナリの保存先。ADR-0004 のキー規則をローカルファイルシステムで実装する。

原本: assets/{sha256の先頭2文字}/{sha256}.{拡張子} (同一内容は共有、既存なら書かない)
派生: derived/{sha256}/thumb.webp, derived/{sha256}/preview.webp

variant からパスを解決する関数は `content_path` の1か所に閉じる。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

Variant = Literal["original", "thumb", "preview"]

_DERIVED_VARIANTS = {"thumb": "thumb.webp", "preview": "preview.webp"}


class AssetStore(Protocol):
    """画像バイナリの読み書きを抽象化する。本線では Azure Blob 実装に差し替える。"""

    def write_original(self, data: bytes, sha256: str, ext: str) -> str:
        """原本を保存し blob_key を返す。既に存在する場合は書き込みをスキップする。"""
        ...

    def write_derived(self, sha256: str, variant: Literal["thumb", "preview"], data: bytes) -> None:
        """派生画像(thumb/preview)を保存する。"""
        ...

    def read(self, blob_key: str) -> bytes: ...

    def exists(self, blob_key: str) -> bool: ...

    def content_path(self, blob_key: str, sha256: str, variant: Variant) -> Path:
        """variant に応じた実体パスを解決する。配信・存在確認はすべてここを経由する。"""
        ...


class LocalFsStore:
    """ローカルファイルシステム実装。ADR-0004 のキー規則をそのまま使う。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def original_key(sha256: str, ext: str) -> str:
        return f"assets/{sha256[:2]}/{sha256}.{ext}"

    def write_original(self, data: bytes, sha256: str, ext: str) -> str:
        key = self.original_key(sha256, ext)
        path = self.root / key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return key

    def write_derived(self, sha256: str, variant: Literal["thumb", "preview"], data: bytes) -> None:
        path = self.root / "derived" / sha256 / _DERIVED_VARIANTS[variant]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, blob_key: str) -> bytes:
        return (self.root / blob_key).read_bytes()

    def exists(self, blob_key: str) -> bool:
        return (self.root / blob_key).exists()

    def content_path(self, blob_key: str, sha256: str, variant: Variant) -> Path:
        if variant == "original":
            return self.root / blob_key
        if variant in _DERIVED_VARIANTS:
            return self.root / "derived" / sha256 / _DERIVED_VARIANTS[variant]
        raise ValueError(f"未知の variant です: {variant}")

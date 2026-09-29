"""画像バイナリの保存先。ローカルファイルシステムで実装する。

原本(ADR-0026。ADR-0004 のキー規則のうちローカルFSの部分を改めた):
  生成:         assets/{プロバイダー}/{モデル}/{YYYY-MM}/{YYYYMMDD-HHMMSS}_{短ID}.{拡張子}
  ComfyUI:      assets/comfyui/{ワークフロー名}/{YYYY-MM}/...
  アップロード: assets/uploads/{YYYY-MM}/...
  マスク:       assets/masks/{YYYY-MM}/...
  スケッチ:     assets/sketches/{YYYY-MM}/...
  日時はサーバーのローカル時刻、短ID は Asset の id の先頭 8 文字。同名があれば `-2` `-3`。
  以前の規則 `assets/{sha256の先頭2文字}/{sha256}.{拡張子}` で保存した原本は移さず、
  `asset.blob_key` のまま読み出す。
派生: derived/{sha256}/thumb.webp, derived/{sha256}/preview.webp(内容から作り直せるので従来どおり)

同じ内容の原本を共有するかどうか(ADR-0026 3章)は DB を持つ側(`domain/assets.py` の
`ingest`)が決める。ストアはキーの決定と書き込み・読み出しだけを扱う。

variant からパスを解決する関数は `content_path` の1か所に閉じる。
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

Variant = Literal["original", "thumb", "preview"]
OriginalKind = Literal["generated", "upload", "mask", "sketch"]

_DERIVED_VARIANTS = {"thumb": "thumb.webp", "preview": "preview.webp"}

# 生成画像以外の種類ごとのフォルダ(ADR-0026 1章)
_KIND_FOLDERS = {"upload": "uploads", "mask": "masks", "sketch": "sketches"}

_SEGMENT_MAX_LENGTH = 64
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 同名のファイルがあったときに試す連番の上限。短ID(uuid の先頭 8 文字)があるので、
# ここまで埋まることは実際には無い。
_MAX_NAME_ATTEMPTS = 1000


def normalize_segment(name: str | None) -> str:
    """プロバイダー名・モデル名・ワークフロー名をフォルダ名にする(ADR-0026 2章)。

    英数字と `.` `_` `-` 以外は `_` に置き換え、連続する `_` を 1 つにまとめ、前後の
    `.` `_` を除く。最大 64 文字。空になれば `unknown`、Windows の予約名なら先頭に `_`。
    """
    text = _UNSAFE_CHARS.sub("_", name or "")
    text = _REPEATED_UNDERSCORES.sub("_", text).strip("._")
    text = text[:_SEGMENT_MAX_LENGTH].strip("._")
    if not text:
        return "unknown"
    # Windows は `CON.txt` のように拡張子が付いていても予約名として扱う
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        text = f"_{text}"[:_SEGMENT_MAX_LENGTH]
    return text


@dataclass(frozen=True)
class OriginalKeyInfo:
    """原本のキーを決めるための情報(ADR-0026 6章)。

    `model` は生成画像のモデル名、ComfyUI ではワークフロー名(保存時点のもの)。
    `created_at` はタイムゾーン付きの時刻で、ファイル名にはサーバーのローカル時刻に
    直して使う。
    """

    kind: OriginalKind
    asset_id: uuid.UUID
    created_at: datetime
    provider: str | None = None
    model: str | None = None


def original_key_base(info: OriginalKeyInfo, ext: str) -> tuple[str, str, str]:
    """(フォルダ, ファイル名の幹, 拡張子) を返す。連番はここでは付けない。"""
    local = info.created_at.astimezone()  # サーバーのローカル時刻(Docker では TZ で変わる)
    month = local.strftime("%Y-%m")
    if info.kind == "generated":
        folder = (
            f"assets/{normalize_segment(info.provider)}/{normalize_segment(info.model)}/{month}"
        )
    else:
        folder = f"assets/{_KIND_FOLDERS[info.kind]}/{month}"
    stem = f"{local.strftime('%Y%m%d-%H%M%S')}_{info.asset_id.hex[:8]}"
    return folder, stem, ext


class AssetStore(Protocol):
    """画像バイナリの読み書きを抽象化する。本線では Azure Blob 実装に差し替える。"""

    def write_original(self, data: bytes, ext: str, info: OriginalKeyInfo) -> str:
        """原本を新しいキーで保存し blob_key を返す。既存のファイルは上書きしない。

        同じ内容の既存ファイルを共有するかどうかは呼び出し側(`ingest`)が決める。
        """
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
    """ローカルファイルシステム実装。原本のキー規則は ADR-0026。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def legacy_original_key(sha256: str, ext: str) -> str:
        """ADR-0026 より前のキー規則(ADR-0004)。既存の Asset はこのキーのまま読み出す。"""
        return f"assets/{sha256[:2]}/{sha256}.{ext}"

    def write_original(self, data: bytes, ext: str, info: OriginalKeyInfo) -> str:
        folder, stem, ext = original_key_base(info, ext)
        directory = self.root / folder
        directory.mkdir(parents=True, exist_ok=True)

        # 一時ファイルに書き切ってから、確保した名前に置き換える。途中で落ちても
        # 書きかけの原本が正式な名前で残らないようにするため。
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=f".{ext}")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            for attempt in range(1, _MAX_NAME_ATTEMPTS + 1):
                name = f"{stem}.{ext}" if attempt == 1 else f"{stem}-{attempt}.{ext}"
                target = directory / name
                try:
                    # 排他作成で名前を確保する(同名があれば次の連番へ)。どの OS でも
                    # 既存のファイルを上書きしない。
                    with open(target, "xb"):
                        pass
                except FileExistsError:
                    continue
                os.replace(tmp_path, target)
                return f"{folder}/{name}"
            raise FileExistsError(f"同名のファイルが多すぎます: {folder}/{stem}.{ext}")
        finally:
            tmp_path.unlink(missing_ok=True)

    def write_derived(self, sha256: str, variant: Literal["thumb", "preview"], data: bytes) -> None:
        path = self.root / "derived" / sha256 / _DERIVED_VARIANTS[variant]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, blob_key: str) -> bytes:
        return (self.root / blob_key).read_bytes()

    def exists(self, blob_key: str) -> bool:
        return (self.root / blob_key).is_file()

    def content_path(self, blob_key: str, sha256: str, variant: Variant) -> Path:
        if variant == "original":
            return self.root / blob_key
        if variant in _DERIVED_VARIANTS:
            return self.root / "derived" / sha256 / _DERIVED_VARIANTS[variant]
        raise ValueError(f"未知の variant です: {variant}")

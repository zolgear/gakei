"""画像バイナリの保存先。既定はローカルファイルシステム(`LocalFsStore`)。

ADR-0028: `STORAGE_BACKEND` で Azure Blob Storage / S3 互換ストレージも選べる(実装は
`app/domain/object_storage.py`)。どのストアでもキーは同じ(以下の規則)。

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

variant からキーを解決する関数は `content_key` の1か所に閉じる。配信や有無の確認は
`content_exists` / `open_content` を経由する(ADR-0028 4章。ローカルFSのパスを外に出さない)。
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from app.config import Settings

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
MAX_NAME_ATTEMPTS = 1000

# 配信でファイルを読むときのチャンクの大きさ
CHUNK_SIZE = 1024 * 1024


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


def candidate_names(stem: str, ext: str) -> Iterator[str]:
    """同名があったときに順に試すファイル名(`{幹}.{拡張子}`、`{幹}-2.{拡張子}`、...)。"""
    for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
        yield f"{stem}.{ext}" if attempt == 1 else f"{stem}-{attempt}.{ext}"


def legacy_original_key(sha256: str, ext: str) -> str:
    """ADR-0026 より前のキー規則(ADR-0004)。既存の Asset はこのキーのまま読み出す。"""
    return f"assets/{sha256[:2]}/{sha256}.{ext}"


def derived_key(sha256: str, variant: Literal["thumb", "preview"]) -> str:
    return f"derived/{sha256}/{_DERIVED_VARIANTS[variant]}"


def content_key(blob_key: str, sha256: str, variant: Variant) -> str:
    """variant に応じたキー。原本は `asset.blob_key`、派生は `derived/{sha256}/...`。"""
    if variant == "original":
        return blob_key
    if variant in _DERIVED_VARIANTS:
        return derived_key(sha256, variant)  # type: ignore[arg-type]
    raise ValueError(f"未知の variant です: {variant}")


@dataclass(frozen=True)
class StoredContent:
    """保存している画像の中身(ADR-0028 4章)。

    `chunks` は中身を先頭から順に返す(1回だけ読める)。`path` はローカルFSのときだけ
    入る実体のパスで、配信で `FileResponse`(Range 要求に応える)を使うために持つ。
    """

    size: int
    chunks: Iterator[bytes]
    path: Path | None = None

    def read_all(self) -> bytes:
        return b"".join(self.chunks)


def _iter_file(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            yield chunk


class AssetStore(Protocol):
    """画像バイナリの読み書きを抽象化する(ADR-0028: ローカルFS / Azure Blob / S3)。"""

    # 保存先の種類(`local` / `azure_blob` / `s3`)
    kind: str

    def write_original(self, data: bytes, ext: str, info: OriginalKeyInfo) -> str:
        """原本を新しいキーで保存し blob_key を返す。既存のファイルは上書きしない。

        同じ内容の既存ファイルを共有するかどうかは呼び出し側(`ingest`)が決める。
        """
        ...

    def write_derived(self, sha256: str, variant: Literal["thumb", "preview"], data: bytes) -> None:
        """派生画像(thumb/preview)を保存する。"""
        ...

    def read(self, blob_key: str) -> bytes:
        """キーの中身を丸ごと返す。無ければ `FileNotFoundError`。"""
        ...

    def exists(self, blob_key: str) -> bool: ...

    def content_exists(self, blob_key: str, sha256: str, variant: Variant) -> bool:
        """variant に応じた中身があるか。"""
        ...

    def open_content(self, blob_key: str, sha256: str, variant: Variant) -> StoredContent | None:
        """variant に応じた中身を開く。無ければ None。配信はここを経由する。"""
        ...

    def check(self) -> None:
        """起動時に、保存先に接続でき読み書きできるかを確かめる。できなければ例外。"""
        ...


class LocalFsStore:
    """ローカルファイルシステム実装。原本のキー規則は ADR-0026。"""

    kind = "local"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    legacy_original_key = staticmethod(legacy_original_key)

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
            for name in candidate_names(stem, ext):
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
        path = self.root / derived_key(sha256, variant)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, blob_key: str) -> bytes:
        return (self.root / blob_key).read_bytes()

    def exists(self, blob_key: str) -> bool:
        return (self.root / blob_key).is_file()

    def local_path(self, blob_key: str, sha256: str, variant: Variant) -> Path:
        """variant に応じた実体のパス(ローカルFSだけ。`AssetStore` には無い)。"""
        return self.root / content_key(blob_key, sha256, variant)

    def content_exists(self, blob_key: str, sha256: str, variant: Variant) -> bool:
        return self.local_path(blob_key, sha256, variant).is_file()

    def open_content(self, blob_key: str, sha256: str, variant: Variant) -> StoredContent | None:
        path = self.local_path(blob_key, sha256, variant)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return None
        if not path.is_file():
            return None
        return StoredContent(size=size, chunks=_iter_file(path), path=path)

    def check(self) -> None:
        """起動時の確認(ローカルFSは `DATA_DIR` を作れていれば足りる)。"""


class StorageUnavailableError(RuntimeError):
    """ADR-0028 2章: 設定の不足や接続・読み書きの失敗で、保存先を使えない(文言は i18n 済み)。"""


class StorageIOError(OSError):
    """オブジェクトストレージの SDK の例外を包み直したもの(ADR-0028、2026-10-01 改訂)。

    SDK の例外は `OSError` の派生ではないので、ローカルFSの頃から `except OSError` で劣化
    させている呼び出し側(MCP のサムネイルなど)が、そのまま劣化できるようにする。文言は
    `_first_line` で鍵・署名を伏せた1行だけにする。
    """


class StoragePermissionError(StorageIOError):
    """権限不足で拒否された(S3 の ListBucket が無いときの 403 など)。文言は i18n 済み。"""


class StorageConditionalWriteError(StorageIOError):
    """条件付きの書き込み(`If-None-Match: *`)を保存先が受け付けない。文言は i18n 済み。"""


def describe_store(settings: Settings) -> str:
    """ログや文言に出す保存先の表示。種類とコンテナ(バケット)名だけ(接続文字列は出さない)。"""
    backend = settings.storage_backend
    if backend == "azure_blob":
        return f"azure_blob ({settings.azure_storage_container or '-'})"
    if backend == "s3":
        return f"s3 ({settings.s3_bucket or '-'})"
    return f"local ({settings.data_dir})"


def open_store(settings: Settings) -> AssetStore:
    """ストアを作り、接続と読み書きを確かめる。だめなら `StorageUnavailableError`。

    起動時(`app.main` の lifespan と `python -m app` の事前確認)と、ストアを使うツールが呼ぶ。
    """
    from app.i18n import console_t

    # 元の例外はつながない(`from None`)。トレースバックに SDK の例外の全文(接続先の URL
    # など)が出ないようにするため。必要な情報は、伏せ字にした1行を文言に入れてある。
    try:
        store = build_store(settings)
    except ValueError as exc:
        # object_storage.StorageConfigError(ValueError の派生)は i18n 済みの文言を持つ。
        raise StorageUnavailableError(str(exc)) from None
    try:
        store.check()
    except Exception as exc:  # noqa: BLE001 - SDK ごとに例外の種類が違うので同じ案内にする
        # StorageIOError は伏せ字済みの文言を持つ(権限不足などの案内が長くなるので、切り詰めない)。
        error = str(exc) if isinstance(exc, StorageIOError) else _first_line(exc)
        raise StorageUnavailableError(
            console_t(
                "app.storageUnavailable",
                storage=describe_store(settings),
                error=error,
            )
        ) from None
    return store


# 例外の文言に紛れ込みうる鍵(接続文字列の AccountKey、SAS の sig など)を伏せる。
_SECRET_PATTERN = re.compile(
    r"(AccountKey|SharedAccessSignature|sig|Signature|X-Amz-Signature|X-Amz-Credential)=[^;&\s]+",
    re.IGNORECASE,
)


def _first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    line = lines[0] if lines else type(exc).__name__
    return _SECRET_PATTERN.sub(r"\1=***", line)[:300]


def build_store(settings: Settings) -> AssetStore:
    """`STORAGE_BACKEND` に応じたストアを作る(ADR-0028 2章)。SDK の import は使うときだけ。

    設定の不足は `StorageConfigError`。接続・読み書きの確認は `check_store` で別に行う。
    """
    backend = settings.storage_backend
    if backend == "local":
        return LocalFsStore(settings.data_dir)
    from app.domain import object_storage

    if backend == "azure_blob":
        return object_storage.build_azure_blob_store(settings)
    if backend == "s3":
        return object_storage.build_s3_store(settings)
    raise ValueError(f"未知の STORAGE_BACKEND です: {backend}")

"""同梱の Mod「ローカルに保存」(ADR-0051 7章)。

Python の Mod の例を兼ねるので、公開モジュール `gakei_mod` と標準ライブラリだけを使い、GAKEI の
内部のモジュールは import しない。

- `run.finished`: 成功した Run の出力を、保存先のフォルダへ書き出す。
- `asset.ingested`: 設定の `ingested_sources` に書いた経路(`upload`、`url` など)で取り込んだ
  画像も書き出す。空なら何もしない(生成の出力は `run.finished` で書く)。

書き方の約束:

- ファイル名はひな形(`filename`)から作る。置き換えの値は英数字と `._-` だけにし、`/` や `..` を
  作らせない。組み立てたパスが保存先の外に出たら失敗にする。
- 同じ名前のファイルがあれば上書きせず、`_1`、`_2` … を付ける。中身が同じファイルが既にあれば
  書かない(送り直しで同じファイルを重ねて作らない)。
- 一時ファイルに書いてから置き換える(書きかけのファイルを残さない)。
- 画像を残さなかった Asset(`deny` の判定を受けた)は書かない。
- 書けなかったら例外にする(配達の失敗として残り、送り直される)。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import string
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

from gakei_mod import (
    SOURCES,
    AssetIngestedSubject,
    Event,
    ImageRef,
    ImageWithheld,
    ModContext,
    RunFinishedSubject,
)

DEFAULT_TEMPLATE = "{date}/{time}_{run_id8}_{index}.{ext}"
PLACEHOLDERS: tuple[str, ...] = (
    "date",
    "time",
    "year",
    "month",
    "day",
    "run_id",
    "run_id8",
    "asset_id",
    "asset_id8",
    "sha256",
    "sha8",
    "index",
    "ext",
    "provider",
    "model",
    "source",
)
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
_UNSAFE_VALUE = re.compile(r"[^A-Za-z0-9._-]")
_FORBIDDEN_IN_SEGMENT = set('<>:"|?*') | {chr(c) for c in range(32)}
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)
}
_VALUE_MAX = 100
_NUMBERED_MAX = 10000


class ExportError(Exception):
    """設定やファイル名が不正、または書けなかった(配達の失敗として残る)。"""


# -- 設定 --------------------------------------------------------------------------


def _root(settings: dict[str, Any]) -> Path:
    folder = str(settings.get("folder") or "").strip()
    if not folder:
        raise ExportError("保存先のフォルダ(folder)を設定してください")
    root = Path(folder).expanduser()
    if not root.is_absolute():
        raise ExportError("保存先のフォルダは絶対パスで指定してください")
    return root


def _sources(settings: dict[str, Any]) -> set[str]:
    raw = str(settings.get("ingested_sources") or "")
    sources = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = sources - set(SOURCES)
    if unknown:
        raise ExportError(
            f"ingested_sources に知らない経路があります: {', '.join(sorted(unknown))}"
            f"(使えるのは {', '.join(SOURCES)})"
        )
    return sources


# -- ファイル名 --------------------------------------------------------------------


def _safe_value(value: object) -> str:
    """置き換えの値。英数字と `._-` 以外は `_` にし、先頭の `.` を落とす(`..` やパスの区切りを
    作らせない)。"""
    text = _UNSAFE_VALUE.sub("_", "" if value is None else str(value))
    return text.lstrip(".")[:_VALUE_MAX]


def placeholder_values(
    event: Event, ref: ImageRef, index: int, run: RunFinishedSubject | None, source: str
) -> dict[str, str]:
    """ひな形の置き換えの値。日時はイベントの時刻を GAKEI のサーバーのローカル時刻にしたもの。"""
    occurred = datetime.fromisoformat(event.occurred_at).astimezone()
    run_id = run.run_id if run is not None else ""
    values = {
        "date": occurred.strftime("%Y-%m-%d"),
        "time": occurred.strftime("%H%M%S"),
        "year": occurred.strftime("%Y"),
        "month": occurred.strftime("%m"),
        "day": occurred.strftime("%d"),
        "run_id": run_id,
        "run_id8": run_id[:8],
        "asset_id": ref.asset_id or "",
        "asset_id8": (ref.asset_id or "")[:8],
        "sha256": ref.sha256,
        "sha8": ref.sha256[:8],
        "index": str(index),
        "ext": _EXTENSIONS.get(ref.mime, "bin"),
        "provider": run.provider if run is not None else "",
        "model": run.model if run is not None else "",
        "source": source,
    }
    return {key: _safe_value(value) for key, value in values.items()}


def _check_segment(segment: str) -> None:
    if segment in ("", ".", ".."):
        raise ExportError(f"ファイル名のひな形から不正なパスができました: {segment!r}")
    if any(c in _FORBIDDEN_IN_SEGMENT for c in segment) or segment.endswith((" ", ".")):
        raise ExportError(f"ファイル名に使えない文字があります: {segment!r}")
    if segment.split(".")[0].upper() in _WINDOWS_RESERVED:
        raise ExportError(f"ファイル名に使えない名前です: {segment!r}")


def render_path(template: str, values: dict[str, str]) -> PurePath:
    """ひな形から保存先の中の相対パスを作る。`/`(と `\\`)でフォルダを分けてよい。知らない
    置き換え、`{x.y}` や `{x:...}` の書き方、絶対パス、`..` は失敗にする。"""
    template = (template or "").strip() or DEFAULT_TEMPLATE
    rendered: list[str] = []
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as e:
        raise ExportError(f"ファイル名のひな形が不正です: {e}") from e
    for literal, field, spec, conversion in parsed:
        rendered.append(literal)
        if field is None:
            continue
        if field not in values or spec or conversion:
            raise ExportError(
                f"ファイル名のひな形に使えない置き換えです: {{{field}}}"
                f"(使えるのは {', '.join('{' + p + '}' for p in PLACEHOLDERS)})"
            )
        rendered.append(values[field])
    text = "".join(rendered)
    if text.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", text):
        raise ExportError("ファイル名のひな形は保存先からの相対パスにしてください")
    segments = re.split(r"[/\\]", text)
    for segment in segments:
        _check_segment(segment)
    return PurePath(*segments)


def resolve_inside(root: Path, relative: PurePath) -> Path:
    """保存先の中のパス。外に出たら失敗(リンクを辿った先も含めて確かめる)。"""
    base = root.resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base) or target == base:
        raise ExportError("保存先のフォルダの外には書けません")
    return target


# -- 書き込み ----------------------------------------------------------------------


def _same_content(path: Path, data: bytes) -> bool:
    try:
        if path.stat().st_size != len(data):
            return False
        return hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(data).digest()
    except OSError:
        return False


def _numbered(path: Path, n: int) -> Path:
    return path.with_name(f"{path.stem}_{n}{path.suffix}")


def _slot(path: Path, data: bytes) -> tuple[Path, bool]:
    """書く先と、同じ中身のファイルが既にあるか。同じ名前で中身が違えば `_1`、`_2` … を付ける
    (上書きしない)。同じ中身が既にあれば、そのパスと True(送り直しで重ねて作らない)。"""
    for n in range(_NUMBERED_MAX):
        candidate = path if n == 0 else _numbered(path, n)
        if not candidate.exists():
            return candidate, False
        if _same_content(candidate, data):
            return candidate, True
    raise ExportError(f"同じ名前のファイルが多すぎます: {path.name}")


def atomic_write(path: Path, data: bytes) -> None:
    """同じフォルダの一時ファイルに書いてから置き換える(書きかけのファイルを残さない)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def write_unique(path: Path, data: bytes) -> tuple[Path, bool]:
    """`path`(同じ名前があれば番号を付けた名前)に書く。書いたパスと、書いたか(同じ中身が既に
    あれば False)を返す。"""
    target, exists = _slot(path, data)
    if not exists:
        atomic_write(target, data)
    return target, not exists


def write_with_sidecar(path: Path, data: bytes, sidecar: bytes | None) -> tuple[Path, bool]:
    """画像と、横に置く JSON(画像と同じ名前で拡張子が `.json`)を書く。"""
    image_path, written = write_unique(path, data)
    if sidecar is not None:
        write_unique(image_path.with_suffix(".json"), sidecar)
    return image_path, written


# -- イベント ----------------------------------------------------------------------


def _sidecar(event: Event, ref: ImageRef, index: int, run: RunFinishedSubject | None) -> bytes:
    """横に置く JSON。送り直しでも同じ中身になるよう、イベントの JSON から作る。"""
    payload = {
        "image": {**asdict(ref), "index": index},
        "run": asdict(run) if run is not None else None,
        "event": event.to_json(),
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


async def _export(
    event: Event,
    ctx: ModContext,
    refs: list[ImageRef],
    run: RunFinishedSubject | None,
    source: str,
) -> None:
    settings = dict(ctx.settings)
    root = _root(settings)
    template = str(settings.get("filename") or DEFAULT_TEMPLATE)
    embed = settings.get("embed_lineage", True) is not False
    write_json = settings.get("write_json") is True
    for index, ref in enumerate(refs):
        values = placeholder_values(event, ref, index, run, source)
        target = resolve_inside(root, render_path(template, values))
        try:
            if embed and ctx.open_image_with_lineage is not None:
                data = await ctx.open_image_with_lineage(ref)
            else:
                data = await ctx.open_image(ref)
        except ImageWithheld:
            # 画像を残さなかった Asset(ADR-0051 4章)は書かない。
            ctx.logger.info("画像の無い Asset を飛ばしました: %s", ref.asset_id)
            continue
        sidecar = _sidecar(event, ref, index, run) if write_json else None
        path, written = await asyncio.to_thread(write_with_sidecar, target, data, sidecar)
        if written:
            ctx.logger.info("保存しました: %s", path)


async def on_run_finished(event: Event, ctx: ModContext) -> None:
    subject = event.subject
    assert isinstance(subject, RunFinishedSubject)
    if subject.status != "succeeded" or not subject.outputs:
        return None
    await _export(event, ctx, list(subject.outputs), subject, "generated")
    return None


async def on_asset_ingested(event: Event, ctx: ModContext) -> None:
    subject = event.subject
    assert isinstance(subject, AssetIngestedSubject)
    if subject.source not in _sources(dict(ctx.settings)):
        return None
    run: RunFinishedSubject | None = None
    if subject.run_id and ctx.get_run is not None:
        run = await ctx.get_run(subject.run_id)
    await _export(event, ctx, [subject.asset], run, subject.source)
    return None

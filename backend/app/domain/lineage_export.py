"""系列の持ち出し(ADR-0037 1章)。

ある Asset を起点に、範囲(`ancestors` = この画像と祖先 / `lineage` = 系列全体)の Asset の原本と、
それを生んだ Run の記録を1つの ZIP にまとめる。

- 範囲の計算は共有リンクと同じ `shares.collect_scope`(見える範囲 ADR-0025、論理削除済みを
  含めない、ノード数の上限)。
- Run は、範囲の Asset を生んだもの(`produced_by_run_id`)だけ。状態は問わない(失敗も証跡。
  ADR-0005)。範囲の Asset を生んでいない Run(失敗して何も出さなかった Run を含む)は入れない。
- Run の入力のうち範囲に入らない Asset(見えない、削除済み、範囲の外)は、ID を出さず数だけ
  (`omitted_input_count`)書く。スケッチの下地(`source_asset_id`)も範囲の外なら null。
- 原本は保存しているバイト列のまま入れる(系列情報を埋め込まない。sha256 で照合できるように)。
- タイトル・タグ・グループ・埋め込みベクトルは入れない。

ZIP は `iter_export_zip` が少しずつ作って返す(ストリーミング。原本を丸ごとメモリに載せない)。
DB を読むのは `build_export` だけで、ZIP を作る間は DB に触れない。
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain.models import AppUser, Run, RunInput
from app.domain.shares import collect_scope
from app.domain.storage import AssetStore

ExportScope = Literal["ancestors", "lineage"]
EXPORT_SCOPES: tuple[str, ...] = ("ancestors", "lineage")

# manifest.json の形式名と版。版を上げるときは取り込み側(`lineage_import.py`)も合わせる。
EXPORT_FORMAT = "gakei.lineage-export/1"
MANIFEST_NAME = "manifest.json"
ASSETS_DIR = "assets"

# ZIP の中のファイル名に使う拡張子(MIME から決める。取り込みでも同じ表を使う)。
EXT_BY_MIME: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


class ExportTargetNotFoundError(Exception):
    """起点の Asset が存在しない・見えない・削除済み(API 層で 404)。"""


class ExportContentMissingError(Exception):
    """範囲の Asset の原本が保存先に無い(API 層で 409)。"""


def asset_file_name(asset_id: uuid.UUID | str, mime: str) -> str:
    """ZIP の中の原本のファイル名。取り込みでは ZIP のパスを信用せず、この名前で引く。"""
    ext = EXT_BY_MIME.get(mime, "bin")
    return f"{ASSETS_DIR}/{asset_id}.{ext}"


@dataclass(frozen=True)
class ExportFile:
    name: str
    blob_key: str
    sha256: str
    size: int


@dataclass
class ExportPlan:
    root_asset_id: uuid.UUID
    scope: ExportScope
    exported_at: datetime
    manifest: dict[str, Any]
    files: list[ExportFile] = field(default_factory=list)
    run_count: int = 0
    truncated: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def filename(self) -> str:
        return f"gakei-lineage-{self.root_asset_id}-{self.exported_at:%Y%m%d}.zip"


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _creator_names(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    """実行者の表示名(`app_user.name`)。メールは出さない(ZIP は他人に渡すもののため)。"""
    if not user_ids:
        return {}
    rows = db.execute(select(AppUser.id, AppUser.name).where(AppUser.id.in_(user_ids))).all()
    return {user_id: name for user_id, name in rows}


def build_export(
    db: Session,
    viewer: CurrentUser,
    root_asset_id: uuid.UUID,
    scope: ExportScope,
    *,
    gakei_version: str,
    now: datetime | None = None,
) -> ExportPlan:
    """書き出す内容(manifest と原本の一覧)を決める。DB を読むのはここだけ。

    起点が `viewer` に見えない・削除済みなら `ExportTargetNotFoundError`。
    """
    from app.domain.shares import ShareTargetNotFoundError

    try:
        result = collect_scope(db, viewer, root_asset_id, scope)
    except ShareTargetNotFoundError as e:
        raise ExportTargetNotFoundError(root_asset_id) from e

    exported_at = now or datetime.now(UTC)
    assets = [asset for asset, _depth in result.assets]
    asset_ids = {a.id for a in assets}

    run_ids = {a.produced_by_run_id for a in assets if a.produced_by_run_id is not None}
    runs: list[Run] = (
        list(db.execute(select(Run).where(Run.id.in_(run_ids))).scalars().all()) if run_ids else []
    )
    runs.sort(key=lambda r: (r.queued_at, str(r.id)))
    inputs_by_run: dict[uuid.UUID, list[RunInput]] = {r.id: [] for r in runs}
    if runs:
        rows = (
            db.execute(
                select(RunInput)
                .where(RunInput.run_id.in_([r.id for r in runs]))
                .order_by(RunInput.run_id, RunInput.position, RunInput.role)
            )
            .scalars()
            .all()
        )
        for row in rows:
            inputs_by_run[row.run_id].append(row)
    names = _creator_names(
        db, {r.created_by_user_id for r in runs if r.created_by_user_id is not None}
    )

    manifest_assets: list[dict[str, Any]] = []
    files: list[ExportFile] = []
    for asset in assets:
        name = asset_file_name(asset.id, asset.mime)
        manifest_assets.append(
            {
                "id": str(asset.id),
                "sha256": asset.sha256,
                "kind": str(asset.kind),
                "mime": asset.mime,
                "width": asset.width,
                "height": asset.height,
                "bytes": asset.bytes,
                "created_at": _iso(asset.created_at),
                "produced_by_run_id": (
                    str(asset.produced_by_run_id) if asset.produced_by_run_id is not None else None
                ),
                "output_index": asset.output_index,
                # 範囲の外の下地は ID を出さない。
                "source_asset_id": (
                    str(asset.source_asset_id) if asset.source_asset_id in asset_ids else None
                ),
                "file": name,
            }
        )
        files.append(
            ExportFile(name=name, blob_key=asset.blob_key, sha256=asset.sha256, size=asset.bytes)
        )

    manifest_runs: list[dict[str, Any]] = []
    for run in runs:
        included = [i for i in inputs_by_run[run.id] if i.asset_id in asset_ids]
        manifest_runs.append(
            {
                "id": str(run.id),
                "provider": run.provider,
                "model": run.model,
                "operation": str(run.operation),
                "prompt": run.prompt,
                "params": run.params or {},
                "status": str(run.status),
                "error_code": run.error_code,
                "error_message": run.error_message,
                "usage": run.usage,
                "created_at": _iso(run.queued_at),
                "finished_at": _iso(run.finished_at),
                "created_by_name": (
                    names.get(run.created_by_user_id)
                    if run.created_by_user_id is not None
                    else None
                ),
                "inputs": [
                    {"asset_id": str(i.asset_id), "role": str(i.role), "position": i.position}
                    for i in included
                ],
                "omitted_input_count": len(inputs_by_run[run.id]) - len(included),
            }
        )

    manifest = {
        "format": EXPORT_FORMAT,
        "exported_at": _iso(exported_at),
        "gakei_version": gakei_version,
        "scope": scope,
        "root_asset_id": str(root_asset_id),
        "truncated": result.truncated,
        "assets": manifest_assets,
        "runs": manifest_runs,
    }
    return ExportPlan(
        root_asset_id=root_asset_id,
        scope=scope,
        exported_at=exported_at,
        manifest=manifest,
        files=files,
        run_count=len(manifest_runs),
        truncated=result.truncated,
    )


def ensure_contents_exist(store: AssetStore, plan: ExportPlan) -> None:
    """ZIP を流し始める前に、原本がすべて保存先にあるかを確かめる(流し始めた後は応答の状態を
    変えられないため)。無ければ `ExportContentMissingError`。"""
    for f in plan.files:
        if not store.content_exists(f.blob_key, f.sha256, "original"):
            raise ExportContentMissingError(f.name)


class _ChunkSink(io.RawIOBase):
    """`zipfile` の書き込み先。書かれたバイト列を溜めておき、`drain` で取り出す。

    seek できない(`tell` が失敗する)ので、`zipfile` はデータ記述子を使う書き方になり、
    書いた分をすぐに送り出せる。
    """

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:  # type: ignore[override]
        data = bytes(b)
        self._chunks.append(data)
        return len(data)

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


def _zip_info(name: str, moment: datetime, compress_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=moment.astimezone(UTC).timetuple()[:6])
    info.compress_type = compress_type
    # 一般的なファイルの属性(-rw-r--r--)。
    info.external_attr = 0o644 << 16
    return info


def iter_export_zip(store: AssetStore, plan: ExportPlan) -> Iterator[bytes]:
    """ZIP を少しずつ作って返す。manifest は圧縮し、原本(すでに圧縮された画像)は無圧縮で入れる。"""
    sink = _ChunkSink()
    manifest_bytes = json.dumps(plan.manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(sink, "w", allowZip64=True) as zf:
        zf.writestr(
            _zip_info(MANIFEST_NAME, plan.exported_at, zipfile.ZIP_DEFLATED), manifest_bytes
        )
        yield sink.drain()
        for f in plan.files:
            content = store.open_content(f.blob_key, f.sha256, "original")
            if content is None:
                # `ensure_contents_exist` の後に消えた。応答の途中なので、ここで打ち切る
                # (ZIP は壊れたものになり、取り込みで断られる)。
                raise ExportContentMissingError(f.name)
            info = _zip_info(f.name, plan.exported_at, zipfile.ZIP_STORED)
            info.file_size = content.size
            with zf.open(info, "w") as out:
                for chunk in content.chunks:
                    out.write(chunk)
                    data = sink.drain()
                    if data:
                        yield data
            data = sink.drain()
            if data:
                yield data
    data = sink.drain()
    if data:
        yield data

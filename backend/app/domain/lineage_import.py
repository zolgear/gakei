"""系列の取り込み(ADR-0037 2章)。`lineage_export.py` が作った ZIP を、取り込んだ利用者の
Asset と Run にする。

検証(どれかに当たれば ZIP 全体を断り、DB には何も書かない):
- ZIP の大きさ、エントリーの数、1ファイルの大きさ、展開後の合計、圧縮率(中身を読む前に、
  ZIP の目録の値で確かめる。読むときも上限を超えて読まない)。ZIP のパスはファイル名として
  使わず、ディスクにも展開しない(原本は manifest の Asset ID から決めた名前で引く)。
- manifest の形式名と版(`gakei.lineage-export/1`)と形(Pydantic)、ID の重複、参照の整合、
  循環が無いこと。
- 各原本の大きさと sha256 が manifest と一致すること。

作るもの(1つのトランザクション。途中で失敗したら rollback して何も残さない):
- 範囲が `descendants` の起点で、作った Run が ZIP に無い生成画像は、アップロードとして作る
  (Run はこしらえない。ADR-0037 1章)。
- Asset: 通常の取り込み(`assets.ingest`)。持ち主は取り込んだ利用者。アップロード・マスク・
  スケッチは、取り込んだ利用者が同じ種類・同じ内容(sha256)の削除されていない Asset を
  持っていれば、それを使う(他人の Asset は使わない。ADR-0025)。生成画像は、取り込んだ Run の
  出力として作る(`produced_by_run_id` と `output_index`)。
- Run: `origin = 'import'` の新しい行。状態は書き出し時のもの(終了状態だけ)で、キューには
  載せない。`created_by_user_id` は取り込んだ利用者。元の記録は `run_import`。入力は取り込んだ
  Asset に付け替え、ZIP に無い入力は付けない。
- 同じ利用者が同じ書き出し元の Run をもう一度取り込んだときは、`run_import.source_run_id` で
  見つけた Run を使い、新しい Run を作らない(その Run の出力も `output_index` と sha256 で
  対応付けて使う)。

保存先に書いた原本は、途中で失敗しても残る(内容で共有するので害はない。ADR-0037 2章)。
"""

from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain import assets as assets_domain
from app.domain.lineage_export import EXPORT_FORMAT, MANIFEST_NAME, asset_file_name
from app.domain.models import (
    RUN_ORIGIN_IMPORT,
    Asset,
    AssetKind,
    Run,
    RunImport,
    RunInput,
    RunStatus,
)
from app.domain.storage import AssetStore
from app.domain.text_safety import sanitize_external
from app.i18n import t

# -- 上限(管理者設定は作らず定数。ADR-0037 2章) ----------------------------------

MAX_ZIP_BYTES = 1024 * 1024 * 1024  # 1 GiB
MAX_ASSETS = 1000
MAX_RUNS = 1000
# 1ファイルの上限はアップロードと同じ(`assets.MAX_UPLOAD_BYTES` 未満)。
MAX_FILE_BYTES = assets_domain.MAX_UPLOAD_BYTES
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
# エントリーの数(Asset の上限 + manifest + ディレクトリなどの余裕)。
MAX_ENTRIES = MAX_ASSETS + 16
# 展開後の合計(原本の合計は ZIP の上限を超えない前提。manifest の分を足す)。
MAX_TOTAL_UNCOMPRESSED = MAX_ZIP_BYTES + MAX_MANIFEST_BYTES
# 圧縮率の上限(画像はほとんど縮まないので、これを超えるものは不正な ZIP とみなす)。
MAX_COMPRESSION_RATIO = 200
_RATIO_CHECK_MIN_BYTES = 1024 * 1024

_TERMINAL_STATUSES = ("succeeded", "failed", "canceled")


class LineageImportError(ValueError):
    """ZIP を断る理由(利用者に見せる文言)。API 層で `status_code` の応答にする。"""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


# -- manifest の形 ------------------------------------------------------------------


class _Model(BaseModel):
    # 同じ版の中での追加の項目は無視する(読めるものだけ読む)。
    model_config = ConfigDict(extra="ignore")


class ManifestRunInput(_Model):
    asset_id: uuid.UUID
    role: Literal["image", "mask", "reference"]
    position: int = Field(ge=0, le=1000)


class ManifestAsset(_Model):
    id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["upload", "generated", "mask", "sketch"]
    mime: Literal["image/png", "image/jpeg", "image/webp"]
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    bytes: int = Field(ge=1)
    created_at: datetime | None = None
    produced_by_run_id: uuid.UUID | None = None
    output_index: int | None = Field(default=None, ge=0, le=1000)
    source_asset_id: uuid.UUID | None = None
    file: str = Field(max_length=200)


class ManifestRun(_Model):
    id: uuid.UUID
    # `run.provider` は String(32)、`run.error_code` は String(32)。
    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=1000)
    operation: Literal["generate", "edit"]
    prompt: str = Field(max_length=200_000)
    params: dict[str, Any] = Field(default_factory=dict)
    status: Literal["succeeded", "failed", "canceled"]
    error_code: str | None = Field(default=None, max_length=32)
    error_message: str | None = Field(default=None, max_length=100_000)
    usage: dict[str, Any] | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    created_by_name: str | None = Field(default=None, max_length=500)
    inputs: list[ManifestRunInput] = Field(default_factory=list, max_length=1000)
    omitted_input_count: int = Field(default=0, ge=0)


class Manifest(_Model):
    format: str
    exported_at: datetime | None = None
    gakei_version: str | None = Field(default=None, max_length=64)
    scope: Literal["ancestors", "descendants", "lineage"]
    root_asset_id: uuid.UUID
    truncated: bool = False
    assets: list[ManifestAsset] = Field(min_length=1, max_length=MAX_ASSETS)
    runs: list[ManifestRun] = Field(default_factory=list, max_length=MAX_RUNS)


# -- ZIP の検証 ---------------------------------------------------------------------


@dataclass
class ValidatedArchive:
    zf: zipfile.ZipFile
    manifest: Manifest
    # Asset ID → ZIP のエントリー
    entries: dict[uuid.UUID, zipfile.ZipInfo] = field(default_factory=dict)


def _fail(message: str) -> LineageImportError:
    return LineageImportError(message)


def _read_bounded(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    """エントリーを最大 `limit` バイトまで読む(目録の値を信用せず、超えたら断る)。CRC の
    不一致は `zipfile` が読み終わりに `BadZipFile` を出す。"""
    try:
        with zf.open(info) as f:
            data = f.read(limit + 1)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError, EOFError) as e:
        # RuntimeError: 暗号化されたエントリー。
        raise _fail(t("lineageImport.corrupted")) from e
    if len(data) > limit:
        raise _fail(t("lineageImport.fileTooLarge"))
    return data


def _check_entries(zf: zipfile.ZipFile) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise _fail(t("lineageImport.tooManyEntries", limit=MAX_ASSETS))
    names = [i.filename for i in infos]
    if len(set(names)) != len(names):
        raise _fail(t("lineageImport.duplicateEntries"))
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise _fail(t("lineageImport.encrypted"))
        total += info.file_size
        if info.file_size >= _RATIO_CHECK_MIN_BYTES and (
            info.compress_size <= 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
        ):
            raise _fail(t("lineageImport.suspiciousCompression"))
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise _fail(t("lineageImport.tooLarge"))


def _is_runless_root(manifest: Manifest, asset: ManifestAsset) -> bool:
    """範囲が `descendants`(この画像と子孫)の起点で、作った Run が ZIP に無い生成画像か。

    書き出し側は、起点を生んだ Run を入れず、`produced_by_run_id` と `output_index` を null に
    する(ADR-0037 1章)。生成画像で Run が無いのは、この場合だけ認める。
    """
    return (
        manifest.scope == "descendants"
        and asset.id == manifest.root_asset_id
        and asset.kind == "generated"
        and asset.produced_by_run_id is None
        and asset.output_index is None
    )


def _check_manifest_consistency(manifest: Manifest) -> None:
    """ID の重複、参照の整合、循環を確かめる。"""
    assets_by_id: dict[uuid.UUID, ManifestAsset] = {}
    for asset in manifest.assets:
        if asset.id in assets_by_id:
            raise _fail(t("lineageImport.invalidManifest", reason="duplicate asset id"))
        assets_by_id[asset.id] = asset
        if asset.file != asset_file_name(asset.id, asset.mime):
            raise _fail(t("lineageImport.invalidManifest", reason="unexpected file name"))
        if asset.bytes >= MAX_FILE_BYTES:
            raise _fail(t("lineageImport.fileTooLarge"))
    runs_by_id: dict[uuid.UUID, ManifestRun] = {}
    for run in manifest.runs:
        if run.id in runs_by_id:
            raise _fail(t("lineageImport.invalidManifest", reason="duplicate run id"))
        runs_by_id[run.id] = run
    if manifest.root_asset_id not in assets_by_id:
        raise _fail(t("lineageImport.invalidManifest", reason="root asset is missing"))

    outputs: set[tuple[uuid.UUID, int]] = set()
    for asset in manifest.assets:
        if _is_runless_root(manifest, asset):
            # 範囲が `descendants` の起点で、作った Run を ZIP に含めていない生成画像。
            pass
        elif asset.kind == "generated":
            if asset.produced_by_run_id not in runs_by_id or asset.output_index is None:
                raise _fail(
                    t("lineageImport.invalidManifest", reason="generated asset without its run")
                )
            key = (asset.produced_by_run_id, asset.output_index)
            if key in outputs:
                raise _fail(t("lineageImport.invalidManifest", reason="duplicate output index"))
            outputs.add(key)
        elif asset.produced_by_run_id is not None:
            raise _fail(
                t("lineageImport.invalidManifest", reason="only generated assets have a run")
            )
        if asset.source_asset_id is not None:
            if asset.kind != "sketch" or asset.source_asset_id not in assets_by_id:
                raise _fail(t("lineageImport.invalidManifest", reason="invalid sketch source"))
    for run in manifest.runs:
        seen: set[tuple[str, int]] = set()
        for run_input in run.inputs:
            if run_input.asset_id not in assets_by_id:
                raise _fail(t("lineageImport.invalidManifest", reason="run input is missing"))
            key = (run_input.role, run_input.position)
            if key in seen:
                raise _fail(t("lineageImport.invalidManifest", reason="duplicate run input"))
            seen.add(key)

    # 循環(入力 Asset → Run → 出力 Asset、下地 → スケッチ)が無いこと。
    graph: dict[tuple[str, uuid.UUID], list[tuple[str, uuid.UUID]]] = {}
    for asset in manifest.assets:
        if asset.produced_by_run_id is not None:
            graph.setdefault(("run", asset.produced_by_run_id), []).append(("asset", asset.id))
        if asset.source_asset_id is not None:
            graph.setdefault(("asset", asset.source_asset_id), []).append(("asset", asset.id))
    for run in manifest.runs:
        for run_input in run.inputs:
            graph.setdefault(("asset", run_input.asset_id), []).append(("run", run.id))
    state: dict[tuple[str, uuid.UUID], int] = {}
    for start in list(graph):
        if state.get(start):
            continue
        stack: list[tuple[tuple[str, uuid.UUID], int]] = [(start, 0)]
        state[start] = 1
        while stack:
            node, index = stack[-1]
            nexts = graph.get(node, [])
            if index < len(nexts):
                stack[-1] = (node, index + 1)
                nxt = nexts[index]
                mark = state.get(nxt, 0)
                if mark == 1:
                    raise _fail(t("lineageImport.invalidManifest", reason="cycle"))
                if mark == 0:
                    state[nxt] = 1
                    stack.append((nxt, 0))
            else:
                state[node] = 2
                stack.pop()


def open_archive(fileobj: IO[bytes]) -> ValidatedArchive:
    """ZIP を開き、原本の中身以外(大きさ・目録・manifest)を確かめる。"""
    fileobj.seek(0, 2)
    size = fileobj.tell()
    fileobj.seek(0)
    if size > MAX_ZIP_BYTES:
        raise LineageImportError(t("lineageImport.zipTooLarge"), status_code=413)
    try:
        zf = zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as e:
        raise _fail(t("lineageImport.notZip")) from e
    try:
        _check_entries(zf)
        try:
            manifest_info = zf.getinfo(MANIFEST_NAME)
        except KeyError as e:
            raise _fail(t("lineageImport.manifestMissing")) from e
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise _fail(t("lineageImport.manifestTooLarge"))
        raw = _read_bounded(zf, manifest_info, MAX_MANIFEST_BYTES)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise _fail(t("lineageImport.invalidManifest", reason="not JSON")) from e
        if not isinstance(data, dict):
            raise _fail(t("lineageImport.invalidManifest", reason="not an object"))
        if data.get("format") != EXPORT_FORMAT:
            raise _fail(t("lineageImport.unsupportedFormat", format=str(data.get("format"))[:80]))
        # ZIP の中身は外から来たテキスト(ADR-0027 2章の追記)。NUL などを除いてから読む。
        try:
            manifest = Manifest.model_validate(sanitize_external(data))
        except ValidationError as e:
            first = e.errors()[0] if e.errors() else {}
            location = ".".join(str(part) for part in first.get("loc", ()))
            raise _fail(t("lineageImport.invalidManifest", reason=location or "schema")) from e
        _check_manifest_consistency(manifest)

        entries: dict[uuid.UUID, zipfile.ZipInfo] = {}
        for asset in manifest.assets:
            try:
                info = zf.getinfo(asset.file)
            except KeyError as e:
                raise _fail(t("lineageImport.fileMissing", name=asset.file)) from e
            if info.file_size != asset.bytes:
                raise _fail(t("lineageImport.fileMismatch", name=asset.file))
            entries[asset.id] = info
    except Exception:
        zf.close()
        raise
    return ValidatedArchive(zf=zf, manifest=manifest, entries=entries)


def _read_asset(archive: ValidatedArchive, asset: ManifestAsset) -> bytes:
    data = _read_bounded(archive.zf, archive.entries[asset.id], MAX_FILE_BYTES)
    if len(data) != asset.bytes or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise _fail(t("lineageImport.fileMismatch", name=asset.file))
    return data


def verify_contents(archive: ValidatedArchive) -> None:
    """各原本の大きさと sha256 が manifest と一致するかを、何も書く前にすべて確かめる。"""
    for asset in archive.manifest.assets:
        _read_asset(archive, asset)


# -- 取り込み -----------------------------------------------------------------------


@dataclass
class ImportResult:
    root_asset_id: uuid.UUID
    asset_count: int
    run_count: int
    created_assets: list[Asset] = field(default_factory=list)
    created_run_count: int = 0


def _owner_condition(column: Any, viewer: CurrentUser) -> Any:
    return column.is_(None) if viewer.id is None else column == viewer.id


def _find_imported_run(db: Session, viewer: CurrentUser, source_run_id: uuid.UUID) -> Run | None:
    """同じ利用者が以前に取り込んだ、同じ書き出し元の Run(削除していないもの)。"""
    return (
        db.execute(
            select(Run)
            .join(RunImport, RunImport.run_id == Run.id)
            .where(
                RunImport.source_run_id == source_run_id,
                Run.origin == RUN_ORIGIN_IMPORT,
                Run.deleted_at.is_(None),
                _owner_condition(Run.created_by_user_id, viewer),
            )
            .order_by(RunImport.imported_at, Run.id)
        )
        .scalars()
        .first()
    )


def _find_own_asset(db: Session, viewer: CurrentUser, kind: str, sha256: str) -> Asset | None:
    """取り込んだ利用者が持っている、同じ種類・同じ内容の、削除されていない Asset
    (アップロード・マスク・スケッチ。生成画像は Run の出力として扱うので対象にしない)。"""
    return (
        db.execute(
            select(Asset)
            .where(
                Asset.sha256 == sha256,
                Asset.kind == kind,
                Asset.produced_by_run_id.is_(None),
                Asset.deleted_at.is_(None),
                _owner_condition(Asset.created_by_user_id, viewer),
            )
            .order_by(Asset.created_at, Asset.id)
        )
        .scalars()
        .first()
    )


def _asset_order(manifest: Manifest) -> list[ManifestAsset]:
    """下地がスケッチより先に来る順(manifest の順を保つ)。循環は検証済み。"""
    by_id = {a.id: a for a in manifest.assets}
    ordered: list[ManifestAsset] = []
    done: set[uuid.UUID] = set()

    def visit(asset: ManifestAsset) -> None:
        chain: list[ManifestAsset] = []
        current: ManifestAsset | None = asset
        while current is not None and current.id not in done:
            chain.append(current)
            current = by_id.get(current.source_asset_id) if current.source_asset_id else None
        for item in reversed(chain):
            if item.id not in done:
                done.add(item.id)
                ordered.append(item)

    for asset in manifest.assets:
        visit(asset)
    return ordered


def import_archive(
    db: Session,
    store: AssetStore,
    viewer: CurrentUser,
    archive: ValidatedArchive,
    *,
    now: datetime | None = None,
) -> ImportResult:
    """検証済みの ZIP を取り込む(flush まで。commit は呼び出し側)。失敗したら
    `LineageImportError`(呼び出し側で rollback)。"""
    manifest = archive.manifest
    imported_at = now or datetime.now(UTC)

    # 1. Run: 以前に取り込んだものを探し、無ければ新しく作る。
    run_map: dict[uuid.UUID, Run] = {}
    new_run_ids: set[uuid.UUID] = set()
    for mrun in manifest.runs:
        existing = _find_imported_run(db, viewer, mrun.id)
        if existing is not None:
            run_map[mrun.id] = existing
            continue
        run = Run(
            provider=mrun.provider,
            model=mrun.model,
            deployment=None,
            operation=mrun.operation,
            prompt=mrun.prompt,
            params=mrun.params,
            status=RunStatus(mrun.status),
            error_code=mrun.error_code,
            error_message=mrun.error_message,
            usage=mrun.usage,
            created_by_user_id=viewer.id,
            origin=RUN_ORIGIN_IMPORT,
            # この GAKEI の列には、この GAKEI に行が入った日時を入れる(元の日時は run_import)。
            queued_at=imported_at,
            started_at=None,
            finished_at=imported_at,
        )
        db.add(run)
        db.flush()
        db.add(
            RunImport(
                run_id=run.id,
                source_run_id=mrun.id,
                source_creator_name=mrun.created_by_name,
                source_created_at=mrun.created_at,
                source_finished_at=mrun.finished_at,
                source_gakei_version=manifest.gakei_version,
                imported_at=imported_at,
            )
        )
        run_map[mrun.id] = run
        new_run_ids.add(run.id)
    db.flush()

    runs_by_source = {r.id: r for r in manifest.runs}

    # 2. Asset: 下地 → スケッチの順に。
    asset_map: dict[uuid.UUID, uuid.UUID] = {}
    created: list[Asset] = []
    for masset in _asset_order(manifest):
        if _is_runless_root(manifest, masset):
            # 作った Run が分からない生成画像は、外から持ち込んだ画像(アップロード)として取り込む。
            # Run をこしらえない(証跡にないものを作らない)。生成画像のまま Run 無しにもしない
            # (生成画像には作った Run がある、という前提を崩さない)。
            kind = AssetKind.UPLOAD
        else:
            kind = AssetKind(masset.kind)
        if kind == AssetKind.GENERATED:
            assert masset.produced_by_run_id is not None and masset.output_index is not None
            run = run_map[masset.produced_by_run_id]
            if run.id not in new_run_ids:
                # 以前に取り込んだ Run の出力。同じ番号の出力があれば内容が同じであること。
                existing = db.execute(
                    select(Asset).where(
                        Asset.produced_by_run_id == run.id,
                        Asset.output_index == masset.output_index,
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    if existing.sha256 != masset.sha256:
                        raise _fail(t("lineageImport.conflictsWithEarlierImport"))
                    asset_map[masset.id] = existing.id
                    continue
            data = _read_asset(archive, masset)
            mrun = runs_by_source[masset.produced_by_run_id]
            asset = _ingest(
                db,
                store,
                data,
                AssetKind.GENERATED,
                produced_by_run_id=run.id,
                output_index=masset.output_index,
                created_by_user_id=viewer.id,
                provider=mrun.provider,
                model=_storage_model(mrun),
            )
        else:
            existing = _find_own_asset(db, viewer, kind.value, masset.sha256)
            if existing is not None:
                asset_map[masset.id] = existing.id
                continue
            data = _read_asset(archive, masset)
            source = asset_map.get(masset.source_asset_id) if masset.source_asset_id else None
            asset = _ingest(
                db,
                store,
                data,
                kind,
                source_asset_id=source,
                created_by_user_id=viewer.id,
            )
        asset_map[masset.id] = asset.id
        created.append(asset)

    # 3. 新しく作った Run の入力を、取り込んだ Asset に付け替える(ZIP に無い入力は付けない)。
    for mrun in manifest.runs:
        run = run_map[mrun.id]
        if run.id not in new_run_ids:
            continue
        for run_input in sorted(mrun.inputs, key=lambda i: (i.position, i.role)):
            db.add(
                RunInput(
                    run_id=run.id,
                    asset_id=asset_map[run_input.asset_id],
                    role=run_input.role,
                    position=run_input.position,
                    created_at=imported_at,
                )
            )
    db.flush()

    return ImportResult(
        root_asset_id=asset_map[manifest.root_asset_id],
        asset_count=len(manifest.assets),
        run_count=len(manifest.runs),
        created_assets=created,
        created_run_count=len(new_run_ids),
    )


def _storage_model(mrun: ManifestRun) -> str:
    """原本の保存先のキー(ADR-0026)に使うモデル名。ComfyUI はワークフロー名。"""
    workflow = mrun.params.get("comfyui_workflow") if isinstance(mrun.params, dict) else None
    if isinstance(workflow, dict) and isinstance(workflow.get("name"), str):
        return workflow["name"]
    return mrun.model


def _ingest(db: Session, store: AssetStore, data: bytes, kind: AssetKind, **kwargs: Any) -> Asset:
    try:
        return assets_domain.ingest(db, store, data, kind, **kwargs)
    except assets_domain.IngestError as e:
        raise LineageImportError(t("lineageImport.invalidImage", reason=str(e))) from e

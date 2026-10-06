"""画像バイトの取り込み。形式検証・保存・派生生成・Asset 行の作成をまとめて行う。

呼び出し側がトランザクション(commit)を制御できるよう、ここでは `session.flush()` までに
留める。同一内容(sha256)は原本のファイルを共有するが(ADR-0026 3章)、`ingest` は
呼び出しごとに新しい Asset 行を作る(重複排除しない)。`POST /api/assets` は、
ダウンロードした PNG の再アップロードを検知して既存の Asset を返せる `ingest_upload`
(ADR-0014)を使う。
"""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import UTC, datetime
from typing import Literal, NamedTuple

from PIL import Image
from PIL import UnidentifiedImageError as PillowUnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain import derivatives
from app.domain.embedded_meta import (
    get_instance_id,
    read_gakei_meta,
    root_asset_ref,
    strip_gakei_chunk,
)
from app.domain.generation_meta import extract_generation_meta
from app.domain.models import Asset, AssetKind, Run, RunInput
from app.domain.storage import AssetStore, OriginalKeyInfo
from app.domain.visibility import get_visible_asset
from app.i18n import t

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_MASK_BYTES = 4 * 1024 * 1024

# Pillow の format 名 -> (mime, 拡張子)
ALLOWED_UPLOAD_FORMATS = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def asset_is_used_as_input(session: Session, asset_id: uuid.UUID) -> bool:
    """その Asset id を入力にした `run_input` が1行でもあるか(Run の状態は問わない)。

    ADR-0010(2026-09-25 追記)の `used_as_input` と、未使用スケッチの再編集の判定に使う。
    """
    return (
        session.execute(select(RunInput.id).where(RunInput.asset_id == asset_id).limit(1)).first()
        is not None
    )


class IngestError(ValueError):
    """アップロードされたバイト列が画像として不正、または制約を満たさない場合。"""


def ingest(
    session: Session,
    store: AssetStore,
    data: bytes,
    kind: AssetKind,
    *,
    produced_by_run_id: uuid.UUID | None = None,
    output_index: int | None = None,
    source_asset_id: uuid.UUID | None = None,
    created_by_user_id: uuid.UUID | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> Asset:
    """画像バイトを検証して保存し、Asset 行を作る。

    `provider` と `model` は kind=generated のときの原本の保存先(ADR-0026 1章)を決める。
    ComfyUI では `model` にワークフロー名を渡す。他の kind では使わない。
    """
    # ADR-0010(2026-09-23 追記): 上描きスケッチの下地 Asset。kind=sketch のときだけ許す。
    if source_asset_id is not None:
        if kind != AssetKind.SKETCH:
            raise IngestError(t("assets.sourceAssetOnlyForSketch"))
        source_asset = session.get(Asset, source_asset_id)
        if source_asset is None or source_asset.deleted_at is not None:
            raise IngestError(t("assets.baseAssetNotFound"))

    if kind == AssetKind.UPLOAD and len(data) >= MAX_UPLOAD_BYTES:
        raise IngestError(t("assets.uploadTooLarge"))
    if kind == AssetKind.MASK and len(data) >= MAX_MASK_BYTES:
        raise IngestError(t("assets.maskTooLarge"))
    # ADR-0010: スケッチは保存後は通常の入力画像として扱うので、マスクの4MB制限は
    # 課さずアップロード画像と同じ上限(MAX_UPLOAD_BYTES)を使う。形式はマスクと同じくPNGのみ。
    if kind == AssetKind.SKETCH and len(data) >= MAX_UPLOAD_BYTES:
        raise IngestError(t("assets.uploadTooLarge"))

    try:
        image = Image.open(io.BytesIO(data))
        image.load()  # ヘッダーだけでなく実際にデコードして検証する
    except (PillowUnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        # 寸法が Pillow の上限(`Image.MAX_IMAGE_PIXELS` の2倍)を超える画像は開く時点で断られる。
        raise IngestError(t("assets.cannotReadImage")) from e

    fmt = image.format
    if fmt not in ALLOWED_UPLOAD_FORMATS:
        raise IngestError(t("assets.unsupportedFormat", format=fmt))
    if kind == AssetKind.MASK and fmt != "PNG":
        raise IngestError(t("assets.maskMustBePng"))
    if kind == AssetKind.SKETCH and fmt != "PNG":
        raise IngestError(t("assets.sketchMustBePng"))

    mime, ext = ALLOWED_UPLOAD_FORMATS[fmt]
    sha256 = hashlib.sha256(data).hexdigest()
    # ADR-0026: ファイル名に Asset の id と作成時刻を使うので、保存の前に決めておく。
    asset_id = uuid.uuid4()
    created_at = _utcnow()
    blob_key = _find_shared_blob_key(session, store, sha256)
    if blob_key is None:
        blob_key = store.write_original(
            data,
            ext,
            OriginalKeyInfo(
                kind=AssetKind(kind).value,
                asset_id=asset_id,
                created_at=created_at,
                provider=provider,
                model=model,
            ),
        )

    thumb_bytes = derivatives.make_thumb(image)
    preview_bytes = derivatives.make_preview(image)
    store.write_derived(sha256, "thumb", thumb_bytes)
    store.write_derived(sha256, "preview", preview_bytes)

    asset = Asset(
        id=asset_id,
        kind=kind,
        sha256=sha256,
        blob_key=blob_key,
        mime=mime,
        width=image.width,
        height=image.height,
        bytes=len(data),
        produced_by_run_id=produced_by_run_id,
        output_index=output_index,
        source_asset_id=source_asset_id,
        created_by_user_id=created_by_user_id,
        created_at=created_at,
    )
    session.add(asset)
    session.flush()
    return asset


def _find_shared_blob_key(session: Session, store: AssetStore, sha256: str) -> str | None:
    """同じ sha256 の既存 Asset(論理削除済みを含む)の原本ファイルを探す(ADR-0026 3章)。

    見つかればそのキーを共有し、新しいファイルは書かない。行はあってもファイルが実在
    しない(手で消された等)ものは飛ばし、どれも無ければ None(新しい規則で書く)。
    古いキー(ADR-0004)の Asset もそのまま共有の対象になる。
    """
    keys = session.scalars(
        select(Asset.blob_key).where(Asset.sha256 == sha256).order_by(Asset.created_at, Asset.id)
    ).all()
    for key in dict.fromkeys(keys):  # 同じキーを共有する行が並ぶので、順序を保って重複を除く
        if store.exists(key):
            return key
    return None


IngestOutcome = Literal["created", "matched_existing"]


class IngestResult(NamedTuple):
    asset: Asset
    outcome: IngestOutcome


def _content_matches_existing(store: AssetStore, existing: Asset, data: bytes) -> bool:
    """`gakei` チャンクを除いたバイト列の sha256 で比べ、一致しなければ画素で比べる
    (ADR-0014 4章)。既存の原本は Blob から読み直す。
    """
    stripped = strip_gakei_chunk(data)
    if hashlib.sha256(stripped).hexdigest() == existing.sha256:
        return True
    try:
        existing_bytes = store.read(existing.blob_key)
        with Image.open(io.BytesIO(existing_bytes)) as existing_image:
            existing_image.load()
            with Image.open(io.BytesIO(stripped)) as new_image:
                new_image.load()
                if existing_image.size != new_image.size:
                    return False
                if existing_image.mode != new_image.mode:
                    return False
                return existing_image.tobytes() == new_image.tobytes()
    except (PillowUnidentifiedImageError, OSError):
        return False


def _ingest_replacement(
    session: Session,
    store: AssetStore,
    data: bytes,
    kind: AssetKind,
    replaces_asset_id: uuid.UUID,
    *,
    viewer: CurrentUser,
) -> IngestResult:
    """未使用スケッチ・未使用マスクの再編集(ADR-0010、2026-09-25 追記)。`kind` は
    `sketch` か `mask`。置き換え元は同じ `kind` である必要がある。

    置き換え元が未使用(`run_input` に1行も無い)なら、新しい Asset は置き換え元の
    `source_asset_id` を引き継ぎ(マスクは常に `source_asset_id` を持たないので None のまま)、
    置き換え元は同じトランザクションで論理削除する(原本は不変のまま、UPDATE するのは
    `deleted_at` だけ)。置き換え元が使用済みなら証跡として残す。スケッチは従来どおり
    `source_asset_id` を置き換え元にして連鎖させるが、マスクは `source_asset_id` を
    持たないので新しいマスクを作るだけにする。

    置き換え元が `viewer` に見えなければ、存在しない場合と同じエラーにする(ADR-0025)。
    """
    created_by_user_id = viewer.id
    target = get_visible_asset(session, viewer, replaces_asset_id)
    if target is None or target.deleted_at is not None:
        raise IngestError(t("assets.replaceTargetNotFound"))
    if target.kind != kind:
        raise IngestError(t("assets.replaceTargetKindMismatch"))

    if asset_is_used_as_input(session, replaces_asset_id):
        chained_source = replaces_asset_id if kind == AssetKind.SKETCH else None
        asset = ingest(
            session,
            store,
            data,
            kind,
            source_asset_id=chained_source,
            created_by_user_id=created_by_user_id,
        )
        return IngestResult(asset, "created")

    inherited_source = target.source_asset_id if kind == AssetKind.SKETCH else None
    asset = ingest(
        session,
        store,
        data,
        kind,
        source_asset_id=inherited_source,
        created_by_user_id=created_by_user_id,
    )
    target.deleted_at = _utcnow()
    session.flush()
    return IngestResult(asset, "created")


def ingest_upload(
    session: Session,
    store: AssetStore,
    data: bytes,
    kind: AssetKind,
    *,
    source_asset_id: uuid.UUID | None = None,
    replaces_asset_id: uuid.UUID | None = None,
    viewer: CurrentUser,
) -> IngestResult:
    """`POST /api/assets` から呼ぶ ingest(ADR-0014 4章・6章、ADR-0010 2026-09-25 追記)。

    `replaces_asset_id` が指定されていれば、未使用スケッチ・未使用マスクの再編集
    (`kind=sketch` か `kind=mask` のときだけ。`source_asset_id` とは同時指定不可)として
    `_ingest_replacement` に委ねる。

    kind=upload のときだけ、埋め込まれた `gakei` メタ情報(`gakei.lineage/1`・`/2` の
    どちらでも)を読む。このインスタンスの既存 Asset を指していて、削除されておらず、
    内容が一致すれば新しい行を作らずその Asset をそのまま返す(`outcome="matched_existing"`)。
    一致しなければ、読み取った内容を自己申告として `origin_meta` に、(同じインスタンスの
    既存 Asset を指していれば)`origin_asset_id` にも記録したうえで新しい Asset を作る。
    kind=upload 以外は常に新規作成する(`outcome="matched_existing"` にはならない)。

    さらに、チャンクを除いたこのファイルの内容の sha256 が、埋め込まれていたメタ情報が
    自称する root の sha256 と一致する(= このファイル自身は改ざんされていない)ときは、
    インスタンスを問わず `origin_ref_asset_id` にその root の asset id を記録する(6章)。
    祖先を埋め込んだ画像のうち、取り込み済みのものにサムネイルを対応付けるのに使う。

    kind=upload で新規作成したときだけ(`matched_existing` で返す既存行には触れない)、
    他のツールや C2PA が埋め込んだ生成メタ情報も読み、追記のみの列 `asset.embedded_meta`
    に保存する(ADR-0018)。読み取りは純粋関数(`domain/generation_meta.py`)で行い、
    何も見つからなければ null のままにする。

    ADR-0025: 作成者は `viewer`(個人モードは null)。上描きの下地・置き換え元は `viewer` に
    見える Asset だけを指定でき、見えなければ存在しない場合と同じエラーにする。既存の Asset を
    返す重複判定(`matched_existing`)は、その Asset が `viewer` に見えるときだけ行い、見えなければ
    新しい Asset として取り込む(ファイルを持っているだけで他人の画像の存在が分からないように。
    同じ内容のファイル自体は保存領域の中で共有する)。見えない既存 Asset を指していた場合も、
    `origin_asset_id` にはこれまでどおり記録する(API はその id を返さない。`api/assets.py`)。
    """
    created_by_user_id = viewer.id
    if source_asset_id is not None and get_visible_asset(session, viewer, source_asset_id) is None:
        raise IngestError(t("assets.baseAssetNotFound"))
    if replaces_asset_id is not None:
        if kind not in (AssetKind.SKETCH, AssetKind.MASK):
            raise IngestError(t("assets.replacesAssetOnlyForSketchOrMask"))
        if source_asset_id is not None:
            raise IngestError(t("assets.cannotCombineSourceAndReplaces"))
        return _ingest_replacement(session, store, data, kind, replaces_asset_id, viewer=viewer)

    if kind != AssetKind.UPLOAD:
        asset = ingest(
            session,
            store,
            data,
            kind,
            source_asset_id=source_asset_id,
            created_by_user_id=created_by_user_id,
        )
        return IngestResult(asset, "created")

    meta = read_gakei_meta(data)
    origin_meta: dict | None = None
    origin_asset_id: uuid.UUID | None = None
    origin_ref_asset_id: uuid.UUID | None = None

    if meta is not None:
        same_instance = meta.get("instance") == get_instance_id(session)
        existing: Asset | None = None
        raw_root_id, claimed_sha256 = root_asset_ref(meta)
        if isinstance(raw_root_id, str):
            try:
                existing = session.get(Asset, uuid.UUID(raw_root_id))
            except ValueError:
                existing = None

        existing_visible = existing is not None and (
            get_visible_asset(session, viewer, existing.id) is not None
        )
        if (
            same_instance
            and existing is not None
            and existing_visible
            and existing.deleted_at is None
            and _content_matches_existing(store, existing, data)
        ):
            return IngestResult(existing, "matched_existing")

        # 一致しなかった内容は自己申告として origin_meta に残すだけで、証跡には使わない。
        origin_meta = meta
        if same_instance and existing is not None:
            origin_asset_id = existing.id

        if isinstance(raw_root_id, str) and claimed_sha256 is not None:
            stripped_sha256 = hashlib.sha256(strip_gakei_chunk(data)).hexdigest()
            if stripped_sha256 == claimed_sha256:
                try:
                    origin_ref_asset_id = uuid.UUID(raw_root_id)
                except ValueError:
                    origin_ref_asset_id = None

    asset = ingest(
        session,
        store,
        data,
        kind,
        source_asset_id=source_asset_id,
        created_by_user_id=created_by_user_id,
    )
    embedded_meta = extract_generation_meta(data)
    if embedded_meta is not None:
        asset.embedded_meta = embedded_meta
    if origin_meta is not None:
        asset.origin_meta = origin_meta
        asset.origin_asset_id = origin_asset_id
        asset.origin_ref_asset_id = origin_ref_asset_id
    if embedded_meta is not None or origin_meta is not None:
        session.flush()
    return IngestResult(asset, "created")


def is_restorable(asset: Asset, produced_by_run: Run | None) -> bool:
    """論理削除された Asset が復元できるかどうか(ADR-0008「Asset の復元」)。

    未削除の Asset は復元の対象ではないので False。生んだ Run が削除済みの場合も
    False(Run 削除は出力をまとめて消す操作なので、個別復元させると整合が崩れる)。
    """
    if asset.deleted_at is None:
        return False
    if produced_by_run is not None and produced_by_run.deleted_at is not None:
        return False
    return True

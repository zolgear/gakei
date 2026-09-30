"""ログイン不要の共有リンク(ADR-0029)。

- 範囲(`single` / `ancestors` / `lineage`)に含まれる Asset は、共有を作る時点で、共有する人に
  見える系列グラフ(ADR-0025、`build_asset_lineage`)から集めて `share_asset` に固定する。
  系列の自称の参照(ADR-0014 の埋め込み(未検証)ノード。別インスタンスの Asset や、見えない
  Asset)は含めない。兄弟の出力(同じ Run の別の出力)は祖先でも子孫でもないので含めない。
- Run は保存しない。含まれる Asset の `produced_by_run_id` から引く(Run と run_input は追記のみ
  なので、Asset の一覧が決まれば、グラフに描く Run と辺も一意に決まる)。
- 公開の側で見せてよいかは `resolve_public_share` の1か所だけで確かめる(可視性の判定
  `app/domain/visibility.py` とは別。ADR-0029 6章)。どの理由で見せられないかは区別しない。
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.domain import annotations as annotations_domain
from app.domain import share_settings
from app.domain.lineage import MAX_DEPTH, MAX_NODES, LineageNotFoundError, build_asset_lineage
from app.domain.models import (
    Asset,
    Run,
    RunInput,
    RunInputRole,
    RunStatus,
    Share,
    ShareAsset,
)
from app.domain.run_views import model_label_from_params, run_text_outputs
from app.domain.schemas import (
    PublicShareAsset,
    PublicShareEdge,
    PublicShareResponse,
    PublicShareRun,
)
from app.domain.visibility import get_visible_asset, share_visible

ScopeName = Literal["single", "ancestors", "lineage"]
SCOPES: tuple[str, ...] = ("single", "ancestors", "lineage")

Variant = Literal["thumb", "preview", "original"]

# 共有のページの URL(`{base}/s/{token}`)。
SHARE_PATH_PREFIX = "/s/"


class ShareTargetNotFoundError(Exception):
    """起点の Asset が存在しない・見えない・削除済み(API 層で 404 にする)。"""


class ShareDisabledError(Exception):
    """共有リンクが管理者設定で無効(API 層で 409 にする)。"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_token() -> str:
    """推測できない乱数のトークン(ADR-0029 1章)。"""
    return secrets.token_urlsafe(32)


def share_url(public_base: str, token: str) -> str:
    return f"{public_base}{SHARE_PATH_PREFIX}{token}"


# -- 範囲の計算(共有を作る時点) ------------------------------------------------


@dataclass
class ScopeResult:
    # (Asset, 作成時の系列グラフでの深さ)。深さ、作成日時の順。
    assets: list[tuple[Asset, int]]
    truncated: bool = False


def _reachable(start: uuid.UUID, adjacency: dict[uuid.UUID, list[uuid.UUID]]) -> set[uuid.UUID]:
    seen: set[uuid.UUID] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        for nxt in adjacency.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def collect_scope(
    db: Session, viewer: CurrentUser, root_asset_id: uuid.UUID, scope: ScopeName
) -> ScopeResult:
    """共有に含める Asset を集める。起点が `viewer` に見えない・削除済みなら
    `ShareTargetNotFoundError`。

    `ancestors` は起点から親の方向に、`lineage` はそれに加えて子の方向にたどって現れる Asset。
    系列グラフ(`build_asset_lineage`)は `viewer` に見えるノードだけで作られる(ADR-0025)。
    そこから埋め込み(未検証)ノードを除き、残った辺だけで起点との到達関係を見る。削除済みの
    Asset は含めない(あとで復元しても、作った時点で確かめていないものは載せない)。
    """
    root = get_visible_asset(db, viewer, root_asset_id)
    if root is None or root.deleted_at is not None:
        raise ShareTargetNotFoundError(root_asset_id)
    if scope == "single":
        return ScopeResult(assets=[(root, 0)])

    down = MAX_DEPTH if scope == "lineage" else 0
    try:
        lineage = build_asset_lineage(
            db, root.id, viewer=viewer, up=MAX_DEPTH, down=down, max_nodes=MAX_NODES
        )
    except LineageNotFoundError as e:
        raise ShareTargetNotFoundError(root_asset_id) from e

    local_nodes = {n.id: n for n in lineage.nodes if not n.embedded}
    parents: dict[uuid.UUID, list[uuid.UUID]] = {}
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for edge in lineage.edges:
        if edge.source not in local_nodes or edge.target not in local_nodes:
            continue
        parents.setdefault(edge.target, []).append(edge.source)
        children.setdefault(edge.source, []).append(edge.target)

    included = _reachable(root.id, parents)
    if scope == "lineage":
        included |= _reachable(root.id, children)
    included.add(root.id)

    depths = {
        node_id: node.depth
        for node_id, node in local_nodes.items()
        if node_id in included and node.type == "asset" and not node.deleted
    }
    rows = db.execute(select(Asset).where(Asset.id.in_(list(depths)))).scalars().all()
    assets = [(a, depths[a.id]) for a in rows if a.deleted_at is None]
    assets.sort(key=lambda pair: (pair[1], pair[0].created_at, str(pair[0].id)))
    return ScopeResult(assets=assets, truncated=lineage.truncated)


# -- 本人向けの操作 ----------------------------------------------------------------


def create_share(
    db: Session,
    viewer: CurrentUser,
    root_asset_id: uuid.UUID,
    scope: ScopeName,
    *,
    allow_original: bool,
) -> tuple[Share, int]:
    """共有を作る(コミットは呼び出し側)。戻り値は (共有, 含まれる Asset の数)。"""
    if not share_settings.is_enabled(db):
        raise ShareDisabledError()
    result = collect_scope(db, viewer, root_asset_id, scope)
    share = Share(
        token=new_token(),
        root_asset_id=root_asset_id,
        scope=scope,
        allow_original=allow_original,
        created_by_user_id=viewer.id,
        access_count=0,
    )
    db.add(share)
    db.flush()
    for asset, depth in result.assets:
        db.add(ShareAsset(share_id=share.id, asset_id=asset.id, depth=depth))
    db.flush()
    return share, len(result.assets)


def list_shares(db: Session, viewer: CurrentUser) -> list[Share]:
    """自分の、取り消していない共有(新しい順)。"""
    return list(
        db.execute(
            select(Share)
            .where(Share.revoked_at.is_(None), share_visible(viewer))
            .order_by(Share.created_at.desc(), Share.id.desc())
        )
        .scalars()
        .all()
    )


def get_own_share(db: Session, viewer: CurrentUser, share_id: uuid.UUID) -> Share | None:
    """自分の、取り消していない共有。他人のもの・取り消し済みは None(呼び出し側で 404)。"""
    share = db.execute(
        select(Share).where(Share.id == share_id, Share.revoked_at.is_(None), share_visible(viewer))
    ).scalar_one_or_none()
    return share


def revoke_share(db: Session, share: Share) -> None:
    """取り消す(論理削除。元に戻せない)。コミットは呼び出し側。"""
    share.revoked_at = _utcnow()


def asset_counts(db: Session, share_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """共有ごとの、含まれる Asset の数(作成時の数。あとで削除した Asset も数える)。"""
    if not share_ids:
        return {}
    rows = db.execute(
        select(ShareAsset.share_id, func.count())
        .where(ShareAsset.share_id.in_(share_ids))
        .group_by(ShareAsset.share_id)
    ).all()
    return {share_id: count for share_id, count in rows}


# -- 公開の側の検査(ここだけで判定する) ------------------------------------------


@dataclass
class PublicAccess:
    share: Share
    root: Asset
    # asset_id を指定したときだけ。
    asset: Asset | None = None


def resolve_public_share(
    db: Session,
    token: str,
    *,
    asset_id: uuid.UUID | None = None,
    variant: Variant | None = None,
) -> PublicAccess | None:
    """公開の API から見せてよいかを確かめる唯一の関数(ADR-0029 6章)。次のどれかが違えば None
    (呼び出し側は理由を区別せず 404 にする):

    - 共有リンクが管理者設定で有効
    - トークンが存在し、取り消されていない
    - 起点の Asset が削除されていない
    - (`asset_id` を指定したとき)その Asset が共有に含まれ、削除されていない
    - (`variant == "original"` のとき)共有が原本を許している
    """
    if not share_settings.is_enabled(db):
        return None
    if not token or len(token) > 64:
        return None
    share = db.execute(select(Share).where(Share.token == token)).scalar_one_or_none()
    if share is None or share.revoked_at is not None:
        return None
    root = db.get(Asset, share.root_asset_id)
    if root is None or root.deleted_at is not None:
        return None
    if variant == "original" and not share.allow_original:
        return None
    access = PublicAccess(share=share, root=root)
    if asset_id is not None:
        member = db.get(ShareAsset, (share.id, asset_id))
        if member is None:
            return None
        asset = db.get(Asset, asset_id)
        if asset is None or asset.deleted_at is not None:
            return None
        access.asset = asset
    return access


def record_access(db: Session, share: Share) -> None:
    """開かれた日時と回数を更新する(証跡ではない。ADR-0029 7章)。コミットは呼び出し側。"""
    db.execute(
        update(Share)
        .where(Share.id == share.id)
        .values(access_count=Share.access_count + 1, last_accessed_at=_utcnow())
    )


# -- 公開の応答 --------------------------------------------------------------------

# 値に Asset・Run の id や画像の sha256 が含まれていれば出さない(ComfyUI の入力画像の名前
# `gakei_{sha256}.png` など。共有の範囲の外の画像を指しうるため)。
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


def _public_scalar(value: Any) -> bool:
    if value is None or isinstance(value, bool | int | float):
        return True
    if isinstance(value, str):
        return not (_UUID_RE.search(value) or _SHA256_RE.search(value))
    return False


def public_params(params: dict[str, Any] | None) -> dict[str, str | int | float | bool | None]:
    """Run の `params` のうち、共有のページに出してよいもの(ADR-0029 3章)。

    - `comfyui_*`(ComfyUI のグラフ全体、入力画像の名前、ワークフローの id など。ADR-0013)は
      出さない。実際に使った seed(`comfyui_seed`)だけは `seed` として出す(Run の詳細画面の
      「同じ設定で再実行」と同じ扱い)。
    - 値が文字列・数値・真偽値・null のものだけ(入れ子の値は出さない)。
    - 値に UUID や sha256 の形の文字列を含むものは出さない(範囲外の Asset・Run を指しうるため)。
    """
    result: dict[str, str | int | float | bool | None] = {}
    if not isinstance(params, dict):
        return result
    for key, value in params.items():
        if not isinstance(key, str) or key.startswith("comfyui_"):
            continue
        if _public_scalar(value):
            result[key] = value
    seed = params.get("comfyui_seed")
    if "seed" not in result and isinstance(seed, int) and not isinstance(seed, bool):
        result["seed"] = seed
    return result


def _public_model(run: Run) -> str:
    # ComfyUI の Run は `model` がワークフローの id なので、名前だけを出す(ADR-0013)。
    label = model_label_from_params(run.params)
    if label is not None:
        return label
    if run.provider == "comfyui" or _UUID_RE.search(run.model):
        return ""
    return run.model


def build_public_response(db: Session, share: Share) -> PublicShareResponse:
    """共有のページの内容。共有に含まれ、削除されていない Asset と、それを作った Run
    (成功して削除されていないもの)と、それらどうしの辺だけを返す。"""
    members = db.execute(
        select(ShareAsset.asset_id, ShareAsset.depth).where(ShareAsset.share_id == share.id)
    ).all()
    depth_by_id = {asset_id: depth for asset_id, depth in members}
    assets = list(
        db.execute(select(Asset).where(Asset.id.in_(list(depth_by_id)), Asset.deleted_at.is_(None)))
        .scalars()
        .all()
    )
    asset_ids = {a.id for a in assets}

    run_ids = {a.produced_by_run_id for a in assets if a.produced_by_run_id is not None}
    runs = (
        list(
            db.execute(
                select(Run).where(
                    Run.id.in_(list(run_ids)),
                    Run.deleted_at.is_(None),
                    Run.status == RunStatus.SUCCEEDED,
                )
            )
            .scalars()
            .all()
        )
        if run_ids
        else []
    )
    runs_by_id = {r.id: r for r in runs}

    edges: list[PublicShareEdge] = []
    for asset in assets:
        if asset.produced_by_run_id in runs_by_id:
            edges.append(
                PublicShareEdge(
                    source=asset.produced_by_run_id,
                    target=asset.id,
                    kind="output",
                    output_index=asset.output_index,
                )
            )
        if asset.source_asset_id is not None and asset.source_asset_id in asset_ids:
            edges.append(
                PublicShareEdge(
                    source=asset.source_asset_id,
                    target=asset.id,
                    kind="sketch_source",
                    primary=True,
                )
            )
        if asset.origin_asset_id is not None and asset.origin_asset_id in asset_ids:
            edges.append(
                PublicShareEdge(source=asset.origin_asset_id, target=asset.id, kind="origin")
            )
    if runs_by_id:
        run_inputs = (
            db.execute(
                select(RunInput)
                .where(RunInput.run_id.in_(list(runs_by_id)))
                .order_by(RunInput.run_id, RunInput.position)
            )
            .scalars()
            .all()
        )
        for run_input in run_inputs:
            if run_input.asset_id not in asset_ids:
                continue
            edges.append(
                PublicShareEdge(
                    source=run_input.asset_id,
                    target=run_input.run_id,
                    kind="input",
                    role=run_input.role,
                    position=run_input.position,
                    primary=run_input.role == RunInputRole.IMAGE and run_input.position == 0,
                )
            )

    titles = annotations_domain.bulk_titles(db, asset_ids)
    assets.sort(key=lambda a: (depth_by_id[a.id], a.created_at, str(a.id)))
    public_assets = [
        PublicShareAsset(
            id=a.id,
            kind=a.kind,
            mime=a.mime,
            width=a.width,
            height=a.height,
            created_at=a.created_at,
            title=titles.get(a.id),
            run_id=a.produced_by_run_id if a.produced_by_run_id in runs_by_id else None,
            depth=depth_by_id[a.id],
        )
        for a in assets
    ]
    public_runs = [
        PublicShareRun(
            id=r.id,
            operation=r.operation,
            model=_public_model(r),
            prompt=r.prompt,
            params=public_params(r.params),
            created_at=r.queued_at,
            text_outputs=run_text_outputs(r),
        )
        for r in sorted(runs, key=lambda r: (r.queued_at, str(r.id)))
    ]
    return PublicShareResponse(
        root_asset_id=share.root_asset_id,
        scope=share.scope,  # type: ignore[arg-type]
        allow_original=share.allow_original,
        created_at=share.created_at,
        assets=public_assets,
        runs=public_runs,
        edges=edges,
    )

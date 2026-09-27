"""系列グラフ(ADR-0009)。ある Asset を起点に、祖先(それを生んだ Run と入力)と
子孫(それを入力に使った Run と出力)を Python での幅優先探索で辿る。

SQLite / PostgreSQL のどちらでも同じコードで動くよう、再帰CTEではなく
「1階層ずつ SELECT する」素朴な実装にしている。ノード数の上限を超えたら
truncated=True を返し、それ以上は辿らない。

`up`/`down` は Asset⇔Run 間の「1ホップ」を1深さとして数える。例えば up=1 なら
起点を生んだ Run までで止め、その Run 自身の入力(さらに祖先の Asset)までは辿らない。
up=2 で1世代分の入力 Asset まで届く、という数え方になる。

祖先方向でたどり着いた Run(起点を生んだ Run に限らず、さらに祖先の Run も含む)は、
n>1 で複数出力を持つことがある。その全出力(起点の兄弟に当たる Asset)を起点と同じ
深さの葉として並べる。兄弟からさらに祖先・子孫へは辿らない。
"""

from __future__ import annotations

import uuid
from collections import deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.assets import asset_is_used_as_input, is_restorable
from app.domain.embedded_meta import get_instance_id, normalize_lineage_meta
from app.domain.models import Asset, Run, RunInput, RunInputRole
from app.domain.run_views import model_label_from_params
from app.domain.schemas import (
    AssetLineageResponse,
    LineageAssetInfo,
    LineageEdge,
    LineageNode,
    LineageRunInfo,
)

# 世代の上限は実質的に設けない(1世代 = Asset→Run→Asset の2ホップ)。探索の量は
# MAX_NODES で抑える(ADR-0014 6章、2026-09-24 追記)。
MAX_DEPTH = 255
MAX_NODES = 1000
DEFAULT_UP = MAX_DEPTH
DEFAULT_DOWN = 3

_PROMPT_PREVIEW_LENGTH = 200


class LineageNotFoundError(Exception):
    """起点の Asset が存在しない場合。"""

    def __init__(self, asset_id: uuid.UUID) -> None:
        self.asset_id = asset_id
        super().__init__(f"asset not found: {asset_id}")


def _asset_node(db: Session, asset: Asset, depth: int) -> LineageNode:
    produced_by_run = None
    if asset.produced_by_run_id is not None:
        produced_by_run = db.get(Run, asset.produced_by_run_id)
    return LineageNode(
        id=asset.id,
        type="asset",
        depth=depth,
        deleted=asset.deleted_at is not None,
        asset=LineageAssetInfo(
            kind=asset.kind,
            width=asset.width,
            height=asset.height,
            mime=asset.mime,
            restorable=is_restorable(asset, produced_by_run),
        ),
    )


def _run_node(run: Run, depth: int) -> LineageNode:
    return LineageNode(
        id=run.id,
        type="run",
        depth=depth,
        deleted=run.deleted_at is not None,
        run=LineageRunInfo(
            operation=run.operation,
            model=run.model,
            model_label=model_label_from_params(run.params),
            status=run.status,
            prompt=run.prompt[:_PROMPT_PREVIEW_LENGTH],
            error_code=run.error_code,
            queued_at=run.queued_at,
        ),
    )


def _valid_embedded_id(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _resolve_embedded_asset(
    db: Session, node_id: uuid.UUID, origin_ref: object = None
) -> uuid.UUID | None:
    """埋め込み(未検証)の asset ノードに対応する、取り込み済みのローカル Asset があれば
    その id(ADR-0014 6章)。`origin_ref_asset_id` が一致し、削除されていないものの
    うち、もっとも新しいもの。

    ノードが `origin_ref`(そのノード自身が、加工せずに取り込まれた画像だったときの元の id)
    を持っていれば、その id でも探す。元の id がこのインスタンスの Asset なら、それを使う。
    """
    candidates = [node_id]
    ref_id = _valid_embedded_id(origin_ref.get("id")) if isinstance(origin_ref, dict) else None
    if ref_id is not None:
        if isinstance(origin_ref, dict) and origin_ref.get("instance") == get_instance_id(db):
            local = db.get(Asset, ref_id)
            if local is not None and local.deleted_at is None:
                return local.id
        candidates.append(ref_id)
    for candidate in candidates:
        row = (
            db.execute(
                select(Asset)
                .where(Asset.origin_ref_asset_id == candidate, Asset.deleted_at.is_(None))
                .order_by(Asset.created_at.desc())
            )
            .scalars()
            .first()
        )
        if row is not None:
            return row.id
    return None


def _embedded_node(db: Session, node_id: uuid.UUID, enode: dict, depth: int) -> LineageNode:
    """`normalize_lineage_meta` が返した1ノードから、埋め込み(未検証)の LineageNode を作る。
    自己申告データなので、想定外の型が来ても Pydantic のバリデーションで落ちないよう防御的に
    値を検査する(ADR-0014 6章)。
    """
    node_type = enode.get("type") if enode.get("type") in ("asset", "run") else "asset"
    asset_info: LineageAssetInfo | None = None
    run_info: LineageRunInfo | None = None
    resolved_asset_id: uuid.UUID | None = None

    if node_type == "asset":
        kind = enode.get("kind")
        if kind not in ("upload", "generated", "mask", "sketch"):
            kind = None
        width = enode.get("width")
        height = enode.get("height")
        mime = enode.get("mime")
        asset_info = LineageAssetInfo(
            kind=kind,
            width=width if isinstance(width, int) and not isinstance(width, bool) else None,
            height=height if isinstance(height, int) and not isinstance(height, bool) else None,
            mime=mime if isinstance(mime, str) else None,
            restorable=False,
        )
        resolved_asset_id = _resolve_embedded_asset(db, node_id, enode.get("origin_ref"))
    else:
        operation = enode.get("operation")
        if operation not in ("generate", "edit"):
            operation = "generate"
        status = enode.get("status")
        if status not in ("queued", "running", "succeeded", "failed", "canceled"):
            status = "succeeded"
        prompt = enode.get("prompt")
        prompt = prompt[:_PROMPT_PREVIEW_LENGTH] if isinstance(prompt, str) else ""
        provider = enode.get("provider")
        params = enode.get("params")
        model = enode.get("model")
        run_info = LineageRunInfo(
            operation=operation,
            model=model if isinstance(model, str) else "",
            model_label=model_label_from_params(params if isinstance(params, dict) else None),
            provider=provider if isinstance(provider, str) else None,
            status=status,
            prompt=prompt,
            error_code=None,
            queued_at=None,
        )

    instance = enode.get("instance")
    return LineageNode(
        id=node_id,
        type=node_type,
        depth=depth,
        deleted=False,
        embedded=True,
        instance=instance if isinstance(instance, str) else None,
        resolved_asset_id=resolved_asset_id,
        embedded_detail=enode,
        asset=asset_info,
        run=run_info,
    )


def _expand_embedded_ancestors(
    db: Session,
    nodes: dict[uuid.UUID, LineageNode],
    edges: list[LineageEdge],
    embedded: dict,
    start_id: uuid.UUID,
    start_depth: int,
    up: int,
    max_nodes: int,
    instance_id: str,
    local_queue: deque[tuple[uuid.UUID, int]],
    substitute_id: uuid.UUID | None = None,
) -> bool:
    """埋め込まれていたグラフ(`normalize_lineage_meta` の戻り値)を、その root から祖先方向へ
    BFS で展開する。辺1本を depth 1 と数え、ローカルの祖先探索と同じ数え方にする
    (ADR-0014 6章)。ノードの `instance` がこのインスタンスで、同じ id のローカル行が
    あれば、そちらを使って通常のローカル祖先探索(`local_queue`)に合流させる。
    ローカル行に同じ id が既にあれば、埋め込みデータで上書きしない。

    `substitute_id` を渡すと、root(`start_id`)のノードは作らず、root への辺をその
    ローカル Asset につなぐ。加工せずに取り込んだ画像では、root は取り込んだ Asset と
    同じ画像なので、2つのノードに分けない。
    """
    truncated = False
    embedded_nodes_by_id: dict[str, dict] = {}
    for n in embedded.get("nodes", []):
        if isinstance(n, dict) and isinstance(n.get("id"), str):
            embedded_nodes_by_id[n["id"]] = n
    edges_by_target: dict[str, list[dict]] = {}
    for e in embedded.get("edges", []):
        if isinstance(e, dict) and isinstance(e.get("target"), str):
            edges_by_target.setdefault(e["target"], []).append(e)

    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(str(start_id), start_depth)])

    while queue:
        node_id_str, depth = queue.popleft()
        if node_id_str in visited:
            continue
        visited.add(node_id_str)
        if -depth > up:
            continue
        enode = embedded_nodes_by_id.get(node_id_str)
        if enode is None:
            continue
        node_uuid = _valid_embedded_id(node_id_str)
        if node_uuid is None:
            continue

        node_type = enode.get("type") if enode.get("type") in ("asset", "run") else None
        resolved_locally = False
        substituted = substitute_id is not None and node_id_str == str(start_id)
        edge_target = substitute_id if substituted else node_uuid

        if not substituted and node_type is not None and enode.get("instance") == instance_id:
            if node_type == "asset":
                local_asset = db.get(Asset, node_uuid)
                if local_asset is not None:
                    if node_uuid not in nodes:
                        if len(nodes) >= max_nodes:
                            truncated = True
                        else:
                            nodes[node_uuid] = _asset_node(db, local_asset, depth)
                            local_queue.append((node_uuid, depth))
                    resolved_locally = True
            else:
                local_run = db.get(Run, node_uuid)
                if local_run is not None:
                    if node_uuid not in nodes:
                        if len(nodes) >= max_nodes:
                            truncated = True
                        else:
                            nodes[node_uuid] = _run_node(local_run, depth)
                    # Run はローカル探索のキューに乗らないので、この関数の中で
                    # 埋め込みの辺を使ってさらに祖先へ辿り続ける(下の共通処理へ続く)。

        if not substituted and not resolved_locally and node_uuid not in nodes:
            if len(nodes) >= max_nodes:
                truncated = True
            else:
                nodes[node_uuid] = _embedded_node(db, node_uuid, enode, depth)

        if resolved_locally:
            # ローカルの Asset に解決できた場合、その祖先はローカルの探索
            # (`_expand_ancestors`)に任せる。ここでは辿らない。
            continue

        for edge in edges_by_target.get(node_id_str, []):
            source_raw = edge.get("source")
            if not isinstance(source_raw, str):
                continue
            source_uuid = _valid_embedded_id(source_raw)
            if source_uuid is None:
                continue
            source_depth = depth - 1
            if -source_depth > up:
                continue
            kind = edge.get("kind")
            if kind not in ("input", "output", "sketch_source", "origin"):
                continue
            role = edge.get("role") if kind == "input" else None
            if role not in (None, "image", "mask", "reference"):
                continue
            position = edge.get("position") if kind == "input" else None
            if position is not None and (
                not isinstance(position, int) or isinstance(position, bool)
            ):
                position = None
            output_index = edge.get("output_index") if kind == "output" else None
            if output_index is not None and (
                not isinstance(output_index, int) or isinstance(output_index, bool)
            ):
                output_index = None
            # 主たる親の判定はローカルの辺と同じにする(sketch_source と、position 0 の
            # image 入力)。output と origin は主たる親にしない。
            is_primary = kind == "sketch_source" or (
                kind == "input" and role == "image" and position == 0
            )
            edges.append(
                LineageEdge(
                    source=source_uuid,
                    target=edge_target,
                    kind=kind,
                    role=role,
                    position=position,
                    output_index=output_index,
                    primary=is_primary,
                )
            )
            queue.append((source_raw, source_depth))

    return truncated


def _expand_ancestors(
    db: Session,
    nodes: dict[uuid.UUID, LineageNode],
    edges: list[LineageEdge],
    root_id: uuid.UUID,
    up: int,
    max_nodes: int,
) -> bool:
    """祖先方向: Asset → produced_by の Run → その Run の全入力 Asset → 再帰。"""
    truncated = False
    asset_expanded: set[uuid.UUID] = set()
    run_inputs_expanded: set[uuid.UUID] = set()
    # output エッジの target(= asset_id)は必ず1本しか無いはず(1つの Asset を生む Run は
    # 1つだけ)なので、target 側で重複を防げる。兄弟出力を足す処理と、個々の Asset を
    # 辿ったときに足す処理の両方から触るので、この関数のトップで共有する。
    output_edge_targets: set[uuid.UUID] = set()
    queue: deque[tuple[uuid.UUID, int]] = deque([(root_id, 0)])

    while queue:
        asset_id, depth = queue.popleft()
        if asset_id in asset_expanded:
            continue
        asset_expanded.add(asset_id)

        asset = db.get(Asset, asset_id)
        if asset is None:
            continue

        # ADR-0010(2026-09-23 追記): 上描きスケッチは source_asset_id で下地 Asset を指す。
        # Run を介さない直接の辺として、主たる親と同じく辿る。
        if asset.source_asset_id is not None:
            source_depth = depth - 1
            if -source_depth > up:
                continue
            if asset.source_asset_id not in nodes:
                if len(nodes) >= max_nodes:
                    truncated = True
                    continue
                source_asset = db.get(Asset, asset.source_asset_id)
                if source_asset is None:
                    continue
                nodes[asset.source_asset_id] = _asset_node(db, source_asset, source_depth)

            edges.append(
                LineageEdge(
                    source=asset.source_asset_id,
                    target=asset_id,
                    kind="sketch_source",
                    primary=True,
                )
            )
            queue.append((asset.source_asset_id, source_depth))
            continue

        # ADR-0014(2026-09-24 追記): ダウンロードした PNG を再アップロードして、埋め込まれた
        # `gakei` メタ情報が指す既存 Asset と内容が一致しなかった場合の由来(未検証)。
        # sketch_source と同じく、Run を介さない直接の辺として辿る。
        if asset.origin_asset_id is not None:
            origin_depth = depth - 1
            if -origin_depth > up:
                continue
            if asset.origin_asset_id not in nodes:
                if len(nodes) >= max_nodes:
                    truncated = True
                    continue
                origin_asset = db.get(Asset, asset.origin_asset_id)
                if origin_asset is None:
                    continue
                nodes[asset.origin_asset_id] = _asset_node(db, origin_asset, origin_depth)

            edges.append(
                LineageEdge(
                    source=asset.origin_asset_id,
                    target=asset_id,
                    kind="origin",
                    primary=False,
                )
            )
            queue.append((asset.origin_asset_id, origin_depth))
            continue

        # ADR-0014 6章(2026-09-24 追記): 由来をこのインスタンスで解決できない
        # (origin_asset_id が無い)取り込み画像。埋め込まれていたグラフを、埋め込み
        # (未検証)ノードとして展開する。DB の行は増やさない。
        if asset.origin_meta is not None:
            embedded = normalize_lineage_meta(asset.origin_meta)
            if embedded is not None:
                embedded_root_id = _valid_embedded_id(embedded.get("root"))
                if embedded_root_id is not None:
                    instance_id = get_instance_id(db)
                    # 加工せずに取り込んだ画像(`origin_ref_asset_id` が root と一致)は、root と
                    # 同じ画像。root のノードは作らず、この Asset を root の位置に置く。
                    collapse = asset.origin_ref_asset_id == embedded_root_id
                    embedded_truncated = _expand_embedded_ancestors(
                        db,
                        nodes,
                        edges,
                        embedded,
                        embedded_root_id,
                        depth if collapse else depth - 1,
                        up,
                        max_nodes,
                        instance_id,
                        queue,
                        substitute_id=asset_id if collapse else None,
                    )
                    if embedded_truncated:
                        truncated = True
                    if not collapse and embedded_root_id in nodes:
                        edges.append(
                            LineageEdge(
                                source=embedded_root_id,
                                target=asset_id,
                                kind="origin",
                                primary=False,
                            )
                        )
            continue

        if asset.produced_by_run_id is None:
            continue
        run_id = asset.produced_by_run_id
        run_depth = depth - 1
        if -run_depth > up:
            continue

        if run_id not in nodes:
            if len(nodes) >= max_nodes:
                truncated = True
                continue
            run = db.get(Run, run_id)
            if run is None:
                continue
            nodes[run_id] = _run_node(run, run_depth)

            # 兄弟出力(n>1 の Run が生んだ他の Asset)を、起点と同じ深さの葉として足す。
            # 今たどっている asset_id 自身もここに含まれるが、output_edge_targets での
            # 重複排除により、下のブロックで二重にエッジが張られることはない。
            sibling_depth = run_depth + 1
            sibling_outputs = (
                db.execute(
                    select(Asset)
                    .where(Asset.produced_by_run_id == run_id)
                    .order_by(Asset.output_index)
                )
                .scalars()
                .all()
            )
            for sibling_asset in sibling_outputs:
                if sibling_asset.id not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    nodes[sibling_asset.id] = _asset_node(db, sibling_asset, sibling_depth)
                if sibling_asset.id in output_edge_targets:
                    continue
                output_edge_targets.add(sibling_asset.id)
                edges.append(
                    LineageEdge(
                        source=run_id,
                        target=sibling_asset.id,
                        kind="output",
                        output_index=sibling_asset.output_index,
                        primary=False,
                    )
                )

        # この Run がこの Asset を生んだ、という output エッジ(兄弟出力の一括処理で
        # 既に足されていれば二重にしない)。
        if asset_id not in output_edge_targets:
            output_edge_targets.add(asset_id)
            edges.append(
                LineageEdge(
                    source=run_id,
                    target=asset_id,
                    kind="output",
                    output_index=asset.output_index,
                    primary=False,
                )
            )

        if run_id in run_inputs_expanded:
            continue
        run_inputs_expanded.add(run_id)

        input_depth = run_depth - 1
        if -input_depth > up:
            continue

        run_inputs = (
            db.execute(
                select(RunInput).where(RunInput.run_id == run_id).order_by(RunInput.position)
            )
            .scalars()
            .all()
        )
        for run_input in run_inputs:
            is_primary = run_input.role == RunInputRole.IMAGE and run_input.position == 0
            if run_input.asset_id not in nodes:
                if len(nodes) >= max_nodes:
                    truncated = True
                    continue
                input_asset = db.get(Asset, run_input.asset_id)
                if input_asset is None:
                    continue
                nodes[run_input.asset_id] = _asset_node(db, input_asset, input_depth)

            edges.append(
                LineageEdge(
                    source=run_input.asset_id,
                    target=run_id,
                    kind="input",
                    role=run_input.role,
                    position=run_input.position,
                    primary=is_primary,
                )
            )
            queue.append((run_input.asset_id, input_depth))

    return truncated


def _is_replaced_away_sketch(db: Session, asset: Asset) -> bool:
    """論理削除済み、かつどの run_input にも無いスケッチか(ADR-0010、2026-09-25 追記)。

    未使用スケッチの再編集で置き換えられて消えた下書きは、子孫方向の探索(sketch_source
    の辺)では辿らない・ノードにも含めない。起点(root)自身はこの判定の対象にしない
    (`build_asset_lineage` が無条件でノードに入れる)。
    """
    if asset.deleted_at is None:
        return False
    return not asset_is_used_as_input(db, asset.id)


def _expand_descendants(
    db: Session,
    nodes: dict[uuid.UUID, LineageNode],
    edges: list[LineageEdge],
    root_id: uuid.UUID,
    down: int,
    max_nodes: int,
) -> bool:
    """子孫方向: Asset → それを入力に使った Run(失敗した Run も含む)→ 出力 Asset → 再帰。"""
    truncated = False
    asset_expanded: set[uuid.UUID] = set()
    run_output_expanded: set[uuid.UUID] = set()
    queue: deque[tuple[uuid.UUID, int]] = deque([(root_id, 0)])

    while queue:
        asset_id, depth = queue.popleft()
        if asset_id in asset_expanded:
            continue
        asset_expanded.add(asset_id)

        # ADR-0010(2026-09-23 追記): この Asset を下地にした上描きスケッチへ、
        # Run を介さない直接の辺で辿る(論理削除済みでも含める。他の Asset 辺の方針と同じ)。
        sketch_depth = depth + 1
        if sketch_depth <= down:
            derived_assets = (
                db.execute(select(Asset).where(Asset.source_asset_id == asset_id)).scalars().all()
            )
            for derived_asset in derived_assets:
                # 置き換えで消えた未使用スケッチの下書きは辿らない(ADR-0010、2026-09-25 追記)。
                if _is_replaced_away_sketch(db, derived_asset):
                    continue
                if derived_asset.id not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    nodes[derived_asset.id] = _asset_node(db, derived_asset, sketch_depth)

                edges.append(
                    LineageEdge(
                        source=asset_id,
                        target=derived_asset.id,
                        kind="sketch_source",
                        primary=True,
                    )
                )
                queue.append((derived_asset.id, sketch_depth))

        # ADR-0014(2026-09-24 追記): この Asset を由来として記録した(内容は一致しなかった)
        # 再アップロード Asset へ、Run を介さない直接の辺で辿る。
        origin_depth = depth + 1
        if origin_depth <= down:
            derived_from_origin = (
                db.execute(select(Asset).where(Asset.origin_asset_id == asset_id)).scalars().all()
            )
            for derived_asset in derived_from_origin:
                if derived_asset.id not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    nodes[derived_asset.id] = _asset_node(db, derived_asset, origin_depth)

                edges.append(
                    LineageEdge(
                        source=asset_id,
                        target=derived_asset.id,
                        kind="origin",
                        primary=False,
                    )
                )
                queue.append((derived_asset.id, origin_depth))

        run_depth = depth + 1
        if run_depth > down:
            continue

        run_inputs = (
            db.execute(select(RunInput).where(RunInput.asset_id == asset_id)).scalars().all()
        )
        for run_input in run_inputs:
            run_id = run_input.run_id
            is_primary = run_input.role == RunInputRole.IMAGE and run_input.position == 0

            if run_id not in nodes:
                if len(nodes) >= max_nodes:
                    truncated = True
                    continue
                run = db.get(Run, run_id)
                if run is None:
                    continue
                nodes[run_id] = _run_node(run, run_depth)

            edges.append(
                LineageEdge(
                    source=asset_id,
                    target=run_id,
                    kind="input",
                    role=run_input.role,
                    position=run_input.position,
                    primary=is_primary,
                )
            )

            if run_id in run_output_expanded:
                continue
            run_output_expanded.add(run_id)

            output_depth = run_depth + 1
            if output_depth > down:
                continue

            outputs = (
                db.execute(
                    select(Asset)
                    .where(Asset.produced_by_run_id == run_id)
                    .order_by(Asset.output_index)
                )
                .scalars()
                .all()
            )
            for output_asset in outputs:
                if output_asset.id not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    nodes[output_asset.id] = _asset_node(db, output_asset, output_depth)

                edges.append(
                    LineageEdge(
                        source=run_id,
                        target=output_asset.id,
                        kind="output",
                        output_index=output_asset.output_index,
                        primary=False,
                    )
                )
                queue.append((output_asset.id, output_depth))

    return truncated


def build_asset_lineage(
    db: Session,
    root_asset_id: uuid.UUID,
    up: int = DEFAULT_UP,
    down: int = DEFAULT_DOWN,
    max_nodes: int = MAX_NODES,
) -> AssetLineageResponse:
    """起点 Asset の系列グラフを組み立てる。

    論理削除された Asset / Run も `deleted: true` を付けて含める(来歴として参照できる)。
    """
    root = db.get(Asset, root_asset_id)
    if root is None:
        raise LineageNotFoundError(root_asset_id)

    nodes: dict[uuid.UUID, LineageNode] = {root.id: _asset_node(db, root, 0)}
    edges: list[LineageEdge] = []

    truncated_up = _expand_ancestors(db, nodes, edges, root.id, up, max_nodes) if up > 0 else False
    truncated_down = (
        _expand_descendants(db, nodes, edges, root.id, down, max_nodes) if down > 0 else False
    )

    # 埋め込み(未検証)のグラフは、ノード数の上限や壊れた自己申告で端点が欠けることがある。
    # 両端がそろっている辺だけを返す。
    edges = [e for e in edges if e.source in nodes and e.target in nodes]

    return AssetLineageResponse(
        root_asset_id=root.id,
        nodes=list(nodes.values()),
        edges=edges,
        truncated=truncated_up or truncated_down,
    )

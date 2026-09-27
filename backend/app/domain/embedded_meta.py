"""PNG に埋め込む系列情報(ADR-0014)。

保存している原本には書き込まない。`GET /api/assets/{id}/content?variant=original&download=1`
の応答にだけ、チャンク単位のバイト操作(Pillow による再エンコードはしない)で
iTXt チャンク(キーワード `gakei`)を追加する。取り除けば原本と同じバイト列に戻る。

チャンクには署名が無く誰でも書き換えられるので、ここで読み取った内容は自己申告として
扱う(`app/domain/assets.py` の `ingest_upload` が照合し、通らなければ `origin_meta` に
そのまま記録するだけで `run_input` などの証跡には書き込まない)。

`gakei.lineage/2`(ADR-0014 6章、2026-09-24 追記)は祖先をすべて系列グラフの形で埋め込む。
`gakei.lineage/1` はその画像を生んだ Run と直接の入力の ID しか持たなかったため、まだ読める
(`normalize_lineage_meta` で同じグラフの形に変換する)が、新しく埋め込むのは v2 だけにする。
"""

from __future__ import annotations

import json
import struct
import uuid
import zlib
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import AppSetting, Asset, Run, RunInput, RunStatus

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
GAKEI_KEYWORD = b"gakei"
SCHEMA_V1 = "gakei.lineage/1"
SCHEMA_V2 = "gakei.lineage/2"
_KNOWN_SCHEMAS = (SCHEMA_V1, SCHEMA_V2)
INSTANCE_SETTING_KEY = "instance.id"

# run.params のうち、画像に埋め込まない項目(ADR-0014 3章)。
# comfyui_prompt: グラフ全体で大きく、ComfyUI 自身が `prompt` チャンクに入れている。
# comfyui_outputs: 出力ノードの一覧で、レシピの復元には不要。
_EXCLUDED_PARAM_KEYS = {"comfyui_prompt", "comfyui_outputs"}

# ADR-0014 6章: 祖先グラフのノード数上限と、本文の上限(超えたら params/prompt を落とす)。
# モジュールレベルの定数にして、テストから monkeypatch できるようにする。
MAX_LINEAGE_NODES = 1000
MAX_LINEAGE_BYTES = 1024 * 1024  # 1 MiB

_VALID_RUN_STATUSES = {"queued", "running", "succeeded", "failed", "canceled"}
_VALID_RUN_INPUT_ROLES = {"image", "mask", "reference"}
_VALID_EDGE_KINDS = {"input", "output", "sketch_source", "origin"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class PngChunk:
    """PNG の1チャンク(type, data)。`iter_png_chunks` / `split_itxt` は
    `app/domain/generation_meta.py`(ADR-0018)からも再利用する。
    """

    type: bytes
    data: bytes

    def encode(self) -> bytes:
        crc = zlib.crc32(self.type + self.data) & 0xFFFFFFFF
        return struct.pack(">I", len(self.data)) + self.type + self.data + struct.pack(">I", crc)


def _is_png(data: bytes) -> bool:
    return data[:8] == PNG_SIGNATURE


def iter_png_chunks(data: bytes) -> list[PngChunk]:
    """PNG のチャンク列を先頭から読み出す。シグネチャが無い、または途中で切れている場合は
    `ValueError`。IEND チャンクに達したら打ち切る。
    """
    if not _is_png(data):
        raise ValueError("PNG signature not found")
    chunks: list[PngChunk] = []
    offset = 8
    total = len(data)
    while offset < total:
        if offset + 8 > total:
            raise ValueError("truncated chunk header")
        (chunk_len,) = struct.unpack(">I", data[offset : offset + 4])
        chunk_type = data[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + chunk_len
        crc_end = data_end + 4
        if chunk_len < 0 or crc_end > total:
            raise ValueError("truncated chunk data")
        chunks.append(PngChunk(type=chunk_type, data=data[data_start:data_end]))
        offset = crc_end
        if chunk_type == b"IEND":
            break
    return chunks


def split_itxt(
    data: bytes,
) -> tuple[bytes, int, int, bytes, bytes, bytes] | None:
    """iTXt チャンクの本文を (keyword, compression_flag, compression_method,
    language_tag, translated_keyword, text) に分解する。壊れていれば None。
    """
    try:
        kw_end = data.index(b"\x00")
    except ValueError:
        return None
    keyword = data[:kw_end]
    rest = data[kw_end + 1 :]
    if len(rest) < 2:
        return None
    compression_flag = rest[0]
    compression_method = rest[1]
    rest = rest[2:]
    try:
        lang_end = rest.index(b"\x00")
    except ValueError:
        return None
    language_tag = rest[:lang_end]
    rest = rest[lang_end + 1 :]
    try:
        trans_end = rest.index(b"\x00")
    except ValueError:
        return None
    translated_keyword = rest[:trans_end]
    text = rest[trans_end + 1 :]
    return keyword, compression_flag, compression_method, language_tag, translated_keyword, text


def _is_gakei_chunk(chunk: PngChunk) -> bool:
    if chunk.type != b"iTXt":
        return False
    parsed = split_itxt(chunk.data)
    return parsed is not None and parsed[0] == GAKEI_KEYWORD


def _make_gakei_chunk(meta: dict[str, Any]) -> PngChunk:
    text = json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    itxt_data = (
        GAKEI_KEYWORD
        + b"\x00"
        + bytes([0])  # compression flag: 0(非圧縮)
        + bytes([0])  # compression method: 0(未使用)
        + b"\x00"  # language tag(空)
        + b"\x00"  # translated keyword(空)
        + text.encode("utf-8")
    )
    return PngChunk(type=b"iTXt", data=itxt_data)


def strip_gakei_chunk(data: bytes) -> bytes:
    """PNG から `gakei` の iTXt チャンクをすべて取り除く。PNG でない、または壊れている
    場合はそのまま返す。既存の `gakei` チャンクが無い場合も、同じバイト列を返す。
    """
    if not _is_png(data):
        return data
    try:
        chunks = iter_png_chunks(data)
    except ValueError:
        return data
    kept = [c for c in chunks if not _is_gakei_chunk(c)]
    if len(kept) == len(chunks):
        return data
    return PNG_SIGNATURE + b"".join(c.encode() for c in kept)


def embed_gakei_chunk(data: bytes, meta: dict[str, Any]) -> bytes:
    """既存の `gakei` チャンクを取り除いたうえで、IEND の直前に新しい `gakei` チャンクを
    1つ挿入する。PNG でない、または壊れている(IEND が無い等)場合はそのまま返す。
    """
    if not _is_png(data):
        return data
    try:
        chunks = iter_png_chunks(data)
    except ValueError:
        return data
    kept = [c for c in chunks if not _is_gakei_chunk(c)]
    gakei_chunk = _make_gakei_chunk(meta)

    out_chunks: list[PngChunk] = []
    inserted = False
    for c in kept:
        if c.type == b"IEND":
            out_chunks.append(gakei_chunk)
            inserted = True
        out_chunks.append(c)
    if not inserted:
        return data
    return PNG_SIGNATURE + b"".join(c.encode() for c in out_chunks)


def read_gakei_meta(data: bytes) -> dict[str, Any] | None:
    """PNG に埋め込まれた `gakei.lineage/1` または `gakei.lineage/2` の JSON を読む。
    PNG でない、チャンクが無い、壊れている、JSON として読めない、スキーマが違う場合は None。
    """
    if not _is_png(data):
        return None
    try:
        chunks = iter_png_chunks(data)
    except ValueError:
        return None
    for chunk in chunks:
        if not _is_gakei_chunk(chunk):
            continue
        parsed = split_itxt(chunk.data)
        if parsed is None:
            return None
        _keyword, compression_flag, _method, _lang, _trans, text_bytes = parsed
        try:
            if compression_flag == 1:
                text_bytes = zlib.decompress(text_bytes)
            meta = json.loads(text_bytes.decode("utf-8"))
        except (zlib.error, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(meta, dict) or meta.get("schema") not in _KNOWN_SCHEMAS:
            return None
        return meta
    return None


def get_instance_id(session: Session) -> str:
    """この GAKEI インスタンス(`DATA_DIR`)の UUID。`app_setting`(キー `instance.id`)に持ち、
    初めて必要になったときに生成して保存する。
    """
    row = session.get(AppSetting, INSTANCE_SETTING_KEY)
    if row is not None and isinstance(row.value, dict):
        existing_id = row.value.get("id")
        if isinstance(existing_id, str):
            return existing_id

    new_id = str(uuid.uuid4())
    if row is None:
        session.add(
            AppSetting(key=INSTANCE_SETTING_KEY, value={"id": new_id}, updated_at=_utcnow())
        )
    else:
        row.value = {"id": new_id}
        row.updated_at = _utcnow()
    # 証跡ではない設定値(app_setting)なので、他の保留中の変更を巻き込まないよう
    # ここで確定する(app/domain/comfyui_connection.py の `_save` と同じ方針)。
    session.commit()
    return new_id


def _compact_json_bytes(meta: dict[str, Any]) -> int:
    text = json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(text.encode("utf-8"))


def _local_asset_node(instance_id: str, asset: Asset) -> dict[str, Any]:
    return {
        "type": "asset",
        "id": str(asset.id),
        "instance": instance_id,
        "sha256": asset.sha256,
        "kind": str(asset.kind),
        "mime": asset.mime,
        "width": asset.width,
        "height": asset.height,
        "created_at": asset.created_at.isoformat(),
    }


def _local_run_node(instance_id: str, run: Run) -> dict[str, Any]:
    params = {k: v for k, v in (run.params or {}).items() if k not in _EXCLUDED_PARAM_KEYS}
    return {
        "type": "run",
        "id": str(run.id),
        "instance": instance_id,
        "provider": run.provider,
        "model": run.model,
        "operation": str(run.operation),
        "prompt": run.prompt,
        "params": params,
        "status": str(run.status),
        "finished_at": run.finished_at.isoformat() if run.finished_at is not None else None,
    }


def _input_edge(source_id: str, target_id: str, role: str, position: int) -> dict[str, Any]:
    return {
        "source": source_id,
        "target": target_id,
        "kind": "input",
        "role": role,
        "position": position,
    }


def _output_edge(source_id: str, target_id: str, output_index: int | None) -> dict[str, Any]:
    return {
        "source": source_id,
        "target": target_id,
        "kind": "output",
        "output_index": output_index,
    }


def _direct_edge(source_id: str, target_id: str, kind: str) -> dict[str, Any]:
    return {"source": source_id, "target": target_id, "kind": kind}


def build_lineage_meta(session: Session, asset: Asset) -> dict[str, Any]:
    """`gakei.lineage/2` の内容を組み立てる(ADR-0014 6章)。起点から祖先方向だけを辿り
    (Run の全入力、上描きの下地、`origin`)、兄弟の出力と子孫は含めない。追記のみの列だけ
    から作るので、同じ Asset からは常に同じ内容(ダウンロード日時などは入れない)ができる。

    祖先に `origin_meta` を持つ Asset(取り込んだ画像)があり、その由来がこのインスタンスで
    解決できない(`origin_asset_id` が null)場合は、`origin_meta` に埋め込まれていたグラフ
    も(そのノードの `instance` を保ったまま)つなげて埋め込む。
    """
    instance_id = get_instance_id(session)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    truncated = False

    def add_node(key: str, node: dict[str, Any]) -> bool:
        nonlocal truncated
        if key in nodes:
            return True
        if len(nodes) >= MAX_LINEAGE_NODES:
            truncated = True
            return False
        nodes[key] = node
        return True

    root_id = str(asset.id)
    add_node(root_id, _local_asset_node(instance_id, asset))

    queue: deque[uuid.UUID] = deque([asset.id])
    visited_assets: set[uuid.UUID] = set()
    visited_runs: set[uuid.UUID] = set()

    while queue:
        current_id = queue.popleft()
        if current_id in visited_assets:
            continue
        visited_assets.add(current_id)
        current = session.get(Asset, current_id)
        if current is None:
            continue
        current_key = str(current_id)
        if current_key not in nodes and not add_node(
            current_key, _local_asset_node(instance_id, current)
        ):
            continue

        # 1) この Asset を生んだ Run と、その Run の全入力(ADR-0003: Edit は最大16入力)。
        if (
            current.produced_by_run_id is not None
            and current.produced_by_run_id not in visited_runs
        ):
            run = session.get(Run, current.produced_by_run_id)
            if run is not None:
                run_key = str(run.id)
                if run_key in nodes or add_node(run_key, _local_run_node(instance_id, run)):
                    visited_runs.add(run.id)
                    edges.append(_output_edge(run_key, current_key, current.output_index))
                    run_inputs = (
                        session.execute(
                            select(RunInput)
                            .where(RunInput.run_id == run.id)
                            .order_by(RunInput.position, RunInput.role)
                        )
                        .scalars()
                        .all()
                    )
                    for run_input in run_inputs:
                        input_asset = session.get(Asset, run_input.asset_id)
                        if input_asset is None:
                            continue
                        input_key = str(run_input.asset_id)
                        if input_key in nodes or add_node(
                            input_key, _local_asset_node(instance_id, input_asset)
                        ):
                            edges.append(
                                _input_edge(
                                    input_key, run_key, str(run_input.role), run_input.position
                                )
                            )
                            if run_input.asset_id not in visited_assets:
                                queue.append(run_input.asset_id)

        # 2) 上描きスケッチの下地(ADR-0010)。
        if current.source_asset_id is not None:
            source_asset = session.get(Asset, current.source_asset_id)
            if source_asset is not None:
                source_key = str(current.source_asset_id)
                if source_key in nodes or add_node(
                    source_key, _local_asset_node(instance_id, source_asset)
                ):
                    edges.append(_direct_edge(source_key, current_key, "sketch_source"))
                    if current.source_asset_id not in visited_assets:
                        queue.append(current.source_asset_id)

        # 3) 由来(ADR-0014)。同じインスタンスで解決できていれば、その Asset をそのまま辿る。
        if current.origin_asset_id is not None:
            origin_asset = session.get(Asset, current.origin_asset_id)
            if origin_asset is not None:
                origin_key = str(current.origin_asset_id)
                if origin_key in nodes or add_node(
                    origin_key, _local_asset_node(instance_id, origin_asset)
                ):
                    edges.append(_direct_edge(origin_key, current_key, "origin"))
                    if current.origin_asset_id not in visited_assets:
                        queue.append(current.origin_asset_id)
        elif current.origin_meta is not None:
            # 解決できない由来(他のインスタンス、または既存 Asset が見つからない): その
            # `origin_meta` に埋め込まれていたグラフをつなげる(6章)。ノードごとの `instance`
            # は保ったままにするので、環境をまたいで何度受け渡しても系列が積み重なる。
            embedded = normalize_lineage_meta(current.origin_meta)
            if embedded is not None:
                embedded_root = embedded.get("root")
                # 加工せずに取り込んだ画像(`origin_ref_asset_id` が root と一致)は、root と同じ
                # 画像。root のノードは入れず、root への辺をこの Asset につなぎ直す。元の id は
                # このノードの `origin_ref` に残す(系列グラフで同じ画像が2つに並ばないように)。
                collapse = isinstance(embedded_root, str) and embedded_root == str(
                    current.origin_ref_asset_id
                )
                for enode in embedded.get("nodes", []):
                    ekey = enode.get("id")
                    if not isinstance(ekey, str):
                        continue
                    if collapse and ekey == embedded_root:
                        nodes[current_key]["origin_ref"] = {
                            "id": ekey,
                            "instance": enode.get("instance"),
                        }
                        continue
                    add_node(ekey, enode)
                for eedge in embedded.get("edges", []):
                    if collapse:
                        eedge = {
                            **eedge,
                            "source": current_key
                            if eedge.get("source") == embedded_root
                            else eedge.get("source"),
                            "target": current_key
                            if eedge.get("target") == embedded_root
                            else eedge.get("target"),
                        }
                    edges.append(eedge)
                if not collapse and isinstance(embedded_root, str) and embedded_root in nodes:
                    edges.append(_direct_edge(embedded_root, current_key, "origin"))
                if embedded.get("truncated"):
                    truncated = True

    meta: dict[str, Any] = {
        "schema": SCHEMA_V2,
        "instance": instance_id,
        "root": root_id,
        "nodes": list(nodes.values()),
        "edges": edges,
        "truncated": truncated,
    }

    if _compact_json_bytes(meta) > MAX_LINEAGE_BYTES:
        meta["truncated"] = True
        # 遠い祖先から params、次いで prompt の順に落とす(6章)。ノードの追加順(BFS)が
        # そのまま「近い祖先が先」になっているので、逆順(末尾から)に処理すれば
        # 「遠い祖先から」になる。
        run_node_list = [n for n in meta["nodes"] if n.get("type") == "run"]
        for n in reversed(run_node_list):
            if "params" not in n:
                continue
            del n["params"]
            if _compact_json_bytes(meta) <= MAX_LINEAGE_BYTES:
                break
        else:
            for n in reversed(run_node_list):
                if "prompt" not in n:
                    continue
                del n["prompt"]
                if _compact_json_bytes(meta) <= MAX_LINEAGE_BYTES:
                    break

    return meta


def root_asset_ref(meta: dict[str, Any]) -> tuple[str | None, str | None]:
    """埋め込まれていたメタ情報から、起点(root)Asset の (id, sha256) を取り出す。
    `gakei.lineage/1`・`/2` の両方に対応する(`app/domain/assets.py` の `ingest_upload` が
    再アップロードの照合と `origin_ref_asset_id` の判定に使う)。
    """
    schema = meta.get("schema")
    if schema == SCHEMA_V1:
        asset = meta.get("asset")
        if isinstance(asset, dict):
            asset_id = asset.get("id")
            sha256 = asset.get("sha256")
            return (
                asset_id if isinstance(asset_id, str) else None,
                sha256 if isinstance(sha256, str) else None,
            )
        return None, None
    if schema == SCHEMA_V2:
        root_id = meta.get("root")
        if not isinstance(root_id, str):
            return None, None
        nodes = meta.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if (
                    isinstance(node, dict)
                    and node.get("type") == "asset"
                    and node.get("id") == root_id
                ):
                    sha256 = node.get("sha256")
                    return root_id, sha256 if isinstance(sha256, str) else None
        return root_id, None
    return None, None


def _valid_uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _normalize_asset_node(raw: dict[str, Any], node_id: str) -> dict[str, Any]:
    width = raw.get("width")
    height = raw.get("height")
    node: dict[str, Any] = {
        "type": "asset",
        "id": node_id,
        "instance": raw.get("instance") if isinstance(raw.get("instance"), str) else None,
        "sha256": raw.get("sha256") if isinstance(raw.get("sha256"), str) else None,
        "kind": raw.get("kind") if isinstance(raw.get("kind"), str) else None,
        "mime": raw.get("mime") if isinstance(raw.get("mime"), str) else None,
        "width": width if isinstance(width, int) and not isinstance(width, bool) else None,
        "height": height if isinstance(height, int) and not isinstance(height, bool) else None,
        "created_at": raw.get("created_at") if isinstance(raw.get("created_at"), str) else None,
    }
    origin_ref = _normalize_origin_ref(raw.get("origin_ref"))
    if origin_ref is not None:
        node["origin_ref"] = origin_ref
    return node


def _normalize_origin_ref(raw: Any) -> dict[str, Any] | None:
    """asset ノードの `origin_ref`(加工せずに取り込んだ画像の、元の asset id と instance)。
    id が UUID でなければ捨てる。
    """
    if not isinstance(raw, dict):
        return None
    ref_id = raw.get("id")
    if not isinstance(ref_id, str):
        return None
    try:
        uuid.UUID(ref_id)
    except ValueError:
        return None
    instance = raw.get("instance")
    return {"id": ref_id, "instance": instance if isinstance(instance, str) else None}


def _normalize_run_node(raw: dict[str, Any], node_id: str) -> dict[str, Any]:
    status = raw.get("status")
    if status not in _VALID_RUN_STATUSES:
        status = str(RunStatus.SUCCEEDED)
    params = raw.get("params")
    return {
        "type": "run",
        "id": node_id,
        "instance": raw.get("instance") if isinstance(raw.get("instance"), str) else None,
        "provider": raw.get("provider") if isinstance(raw.get("provider"), str) else None,
        "model": raw.get("model") if isinstance(raw.get("model"), str) else None,
        "operation": raw.get("operation") if isinstance(raw.get("operation"), str) else None,
        "prompt": raw.get("prompt") if isinstance(raw.get("prompt"), str) else None,
        "params": params if isinstance(params, dict) else {},
        "status": status,
        "finished_at": raw.get("finished_at") if isinstance(raw.get("finished_at"), str) else None,
    }


def _cap_add_node(
    nodes: dict[str, dict[str, Any]], key: str, node: dict[str, Any], max_nodes: int
) -> bool:
    if key in nodes:
        return True
    if len(nodes) >= max_nodes:
        return False
    nodes[key] = node
    return True


def _normalize_v2(meta: dict[str, Any]) -> dict[str, Any] | None:
    root_id = _valid_uuid(meta.get("root"))
    if root_id is None:
        return None
    instance = meta.get("instance") if isinstance(meta.get("instance"), str) else None

    raw_nodes = meta.get("nodes")
    if not isinstance(raw_nodes, list):
        raw_nodes = []
    truncated = bool(meta.get("truncated")) or len(raw_nodes) > MAX_LINEAGE_NODES

    nodes: dict[str, dict[str, Any]] = {}
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        node_id = _valid_uuid(raw.get("id"))
        if node_id is None:
            continue
        node_type = raw.get("type")
        if node_type == "asset":
            node = _normalize_asset_node(raw, node_id)
        elif node_type == "run":
            node = _normalize_run_node(raw, node_id)
        else:
            continue
        if not _cap_add_node(nodes, node_id, node, MAX_LINEAGE_NODES):
            truncated = True

    if root_id not in nodes:
        return None

    raw_edges = meta.get("edges")
    if not isinstance(raw_edges, list):
        raw_edges = []
    edges: list[dict[str, Any]] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        if kind not in _VALID_EDGE_KINDS:
            continue
        source_id = _valid_uuid(raw.get("source"))
        target_id = _valid_uuid(raw.get("target"))
        if source_id is None or target_id is None:
            continue
        if source_id not in nodes or target_id not in nodes:
            continue
        if kind == "input":
            role = raw.get("role")
            if role not in _VALID_RUN_INPUT_ROLES:
                continue
            position = raw.get("position")
            if not isinstance(position, int) or isinstance(position, bool):
                continue
            edges.append(_input_edge(source_id, target_id, role, position))
        elif kind == "output":
            output_index = raw.get("output_index")
            if isinstance(output_index, bool) or not (
                output_index is None or isinstance(output_index, int)
            ):
                output_index = None
            edges.append(_output_edge(source_id, target_id, output_index))
        else:
            edges.append(_direct_edge(source_id, target_id, kind))

    return {
        "schema": SCHEMA_V2,
        "instance": instance,
        "root": root_id,
        "nodes": list(nodes.values()),
        "edges": edges,
        "truncated": truncated,
    }


def _normalize_v1(meta: dict[str, Any]) -> dict[str, Any] | None:
    raw_asset = meta.get("asset")
    if not isinstance(raw_asset, dict):
        return None
    root_id = _valid_uuid(raw_asset.get("id"))
    if root_id is None:
        return None
    instance = meta.get("instance") if isinstance(meta.get("instance"), str) else None

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    truncated = False

    sha256 = raw_asset.get("sha256")
    nodes[root_id] = {
        "type": "asset",
        "id": root_id,
        "instance": instance,
        "sha256": sha256 if isinstance(sha256, str) else None,
        "kind": raw_asset.get("kind") if isinstance(raw_asset.get("kind"), str) else None,
        "mime": None,
        "width": None,
        "height": None,
        "created_at": raw_asset.get("created_at")
        if isinstance(raw_asset.get("created_at"), str)
        else None,
    }

    raw_run = meta.get("run")
    if isinstance(raw_run, dict):
        run_id = _valid_uuid(raw_run.get("id"))
        if run_id is not None and _cap_add_node(
            nodes,
            run_id,
            _normalize_run_node(dict(raw_run, instance=instance), run_id),
            MAX_LINEAGE_NODES,
        ):
            output_index = raw_asset.get("output_index")
            if isinstance(output_index, bool) or not (
                output_index is None or isinstance(output_index, int)
            ):
                output_index = None
            edges.append(_output_edge(run_id, root_id, output_index))

            raw_inputs = meta.get("inputs")
            if isinstance(raw_inputs, list):
                for raw_input in raw_inputs:
                    if not isinstance(raw_input, dict):
                        continue
                    input_id = _valid_uuid(raw_input.get("asset_id"))
                    if input_id is None:
                        continue
                    role = raw_input.get("role")
                    if role not in _VALID_RUN_INPUT_ROLES:
                        continue
                    position = raw_input.get("position")
                    if not isinstance(position, int) or isinstance(position, bool):
                        continue
                    input_sha256 = raw_input.get("sha256")
                    input_node = {
                        "type": "asset",
                        "id": input_id,
                        "instance": instance,
                        "sha256": input_sha256 if isinstance(input_sha256, str) else None,
                        "kind": None,
                        "mime": None,
                        "width": None,
                        "height": None,
                        "created_at": None,
                    }
                    if not _cap_add_node(nodes, input_id, input_node, MAX_LINEAGE_NODES):
                        truncated = True
                        continue
                    edges.append(_input_edge(input_id, run_id, role, position))

    raw_source = meta.get("source_asset")
    if isinstance(raw_source, dict):
        source_id = _valid_uuid(raw_source.get("id"))
        if source_id is not None:
            source_sha256 = raw_source.get("sha256")
            source_node = {
                "type": "asset",
                "id": source_id,
                "instance": instance,
                "sha256": source_sha256 if isinstance(source_sha256, str) else None,
                "kind": None,
                "mime": None,
                "width": None,
                "height": None,
                "created_at": None,
            }
            if _cap_add_node(nodes, source_id, source_node, MAX_LINEAGE_NODES):
                edges.append(_direct_edge(source_id, root_id, "sketch_source"))
            else:
                truncated = True

    return {
        "schema": SCHEMA_V2,
        "instance": instance,
        "root": root_id,
        "nodes": list(nodes.values()),
        "edges": edges,
        "truncated": truncated,
    }


def normalize_lineage_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """埋め込まれていた `gakei.lineage/1`・`/2` の JSON を、v2 のグラフの形
    (`{schema, instance, root, nodes, edges, truncated}`)に正規化する(ADR-0014 6章)。

    署名の無い自己申告データなので、壊れていても例外を投げない。ID が UUID として読めない
    ノード・辺、存在しないノードを参照する辺、知らない `kind` の辺は黙って捨てる。ノード数は
    200 件に丸める(超えていれば `truncated: true`)。root が特定できなければ None。
    """
    if not isinstance(meta, dict):
        return None
    schema = meta.get("schema")
    if schema == SCHEMA_V2:
        return _normalize_v2(meta)
    if schema == SCHEMA_V1:
        return _normalize_v1(meta)
    return None

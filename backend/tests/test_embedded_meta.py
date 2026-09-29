"""domain.embedded_meta の単体テスト(ADR-0014)。

PNG チャンク単位の埋め込み・除去・読み取りの往復と、`build_lineage_meta` が
追記のみの列だけから決定的な内容を組み立てることを確認する。実 API は呼ばない。
"""

from __future__ import annotations

import io
import json
import struct
import uuid
import zlib
from datetime import UTC, datetime

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlalchemy.orm import sessionmaker

from app.auth.identity import LOCAL_ADMIN
from app.domain.assets import ingest
from app.domain.embedded_meta import (
    MAX_LINEAGE_NODES,
    SCHEMA_V1,
    SCHEMA_V2,
    build_lineage_meta,
    embed_gakei_chunk,
    get_instance_id,
    normalize_lineage_meta,
    read_gakei_meta,
    strip_gakei_chunk,
)
from app.domain.models import AssetKind, Run, RunInput, RunInputRole, RunOperation, RunStatus
from app.domain.storage import LocalFsStore
from tests.conftest import extra_database_url, make_png_bytes

_SAMPLE_META = {
    "schema": "gakei.lineage/1",
    "instance": "11111111-1111-1111-1111-111111111111",
    "asset": {
        "id": "22222222-2222-2222-2222-222222222222",
        "sha256": "a" * 64,
        "kind": "generated",
        "output_index": 0,
        "created_at": "2026-09-24T00:00:00+00:00",
    },
}


def test_embed_then_read_round_trip() -> None:
    data = make_png_bytes()
    embedded = embed_gakei_chunk(data, _SAMPLE_META)
    assert embedded != data
    meta = read_gakei_meta(embedded)
    assert meta == _SAMPLE_META


def test_embed_then_read_round_trip_with_japanese_prompt() -> None:
    data = make_png_bytes()
    meta = dict(_SAMPLE_META)
    meta["run"] = {
        "id": str(uuid.uuid4()),
        "provider": "openai",
        "model": "gpt-image-2.5-sunburst",
        "operation": "generate",
        "prompt": "夕焼けの中を歩く猫、水彩画風、日本語のプロンプト",
        "params": {"size": "1024x1024"},
        "finished_at": "2026-09-24T00:00:00+00:00",
    }
    embedded = embed_gakei_chunk(data, meta)
    read_back = read_gakei_meta(embedded)
    assert read_back == meta
    assert read_back["run"]["prompt"] == "夕焼けの中を歩く猫、水彩画風、日本語のプロンプト"


def test_strip_after_embed_returns_original_bytes_exactly() -> None:
    data = make_png_bytes()
    embedded = embed_gakei_chunk(data, _SAMPLE_META)
    assert strip_gakei_chunk(embedded) == data


def test_strip_without_gakei_chunk_is_noop() -> None:
    data = make_png_bytes()
    assert strip_gakei_chunk(data) == data


def test_embedding_twice_keeps_exactly_one_gakei_chunk() -> None:
    data = make_png_bytes()
    once = embed_gakei_chunk(data, _SAMPLE_META)
    other_meta = dict(_SAMPLE_META, instance="33333333-3333-3333-3333-333333333333")
    twice = embed_gakei_chunk(once, other_meta)

    # 除去すれば元のバイト列に戻る(gakei チャンクは1つしか残っていない)。
    assert strip_gakei_chunk(twice) == data
    # 読み取れる内容は2回目に埋め込んだものだけ。
    assert read_gakei_meta(twice) == other_meta

    gakei_chunk_count = twice.count(b"iTXtgakei\x00")
    assert gakei_chunk_count == 1


def test_embedded_png_has_valid_crc_and_pillow_can_open_and_load() -> None:
    data = make_png_bytes(width=200, height=100)
    embedded = embed_gakei_chunk(data, _SAMPLE_META)

    # チャンクを手で辿って CRC を検算する。
    offset = 8
    found_iend = False
    while offset < len(embedded):
        (chunk_len,) = struct.unpack(">I", embedded[offset : offset + 4])
        chunk_type = embedded[offset + 4 : offset + 8]
        chunk_data = embedded[offset + 8 : offset + 8 + chunk_len]
        crc_bytes = embedded[offset + 8 + chunk_len : offset + 12 + chunk_len]
        (stored_crc,) = struct.unpack(">I", crc_bytes)
        assert zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF == stored_crc
        offset += 12 + chunk_len
        if chunk_type == b"IEND":
            found_iend = True
            break
    assert found_iend

    with Image.open(io.BytesIO(embedded)) as image:
        image.load()
        assert image.size == (200, 100)


def test_other_text_chunks_are_preserved() -> None:
    image = Image.new("RGB", (16, 16), (10, 20, 30))
    info = PngInfo()
    info.add_text("Comment", "not gakei")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    data = buffer.getvalue()
    assert b"Comment" in data

    embedded = embed_gakei_chunk(data, _SAMPLE_META)
    assert b"Comment" in embedded
    with Image.open(io.BytesIO(embedded)) as reopened:
        assert reopened.info.get("Comment") == "not gakei"
    assert read_gakei_meta(embedded) == _SAMPLE_META


def test_non_png_bytes_are_unchanged_by_embed_and_read_returns_none() -> None:
    image = Image.new("RGB", (16, 16), (0, 0, 0))

    jpeg_buffer = io.BytesIO()
    image.save(jpeg_buffer, format="JPEG")
    jpeg_data = jpeg_buffer.getvalue()
    assert embed_gakei_chunk(jpeg_data, _SAMPLE_META) == jpeg_data
    assert strip_gakei_chunk(jpeg_data) == jpeg_data
    assert read_gakei_meta(jpeg_data) is None

    webp_buffer = io.BytesIO()
    image.save(webp_buffer, format="WEBP")
    webp_data = webp_buffer.getvalue()
    assert embed_gakei_chunk(webp_data, _SAMPLE_META) == webp_data
    assert strip_gakei_chunk(webp_data) == webp_data
    assert read_gakei_meta(webp_data) is None


def test_read_returns_none_for_garbage_bytes() -> None:
    assert read_gakei_meta(b"not a png at all") is None
    assert read_gakei_meta(b"") is None


def test_read_returns_none_for_malformed_json_in_chunk() -> None:
    data = make_png_bytes()
    # embed_gakei_chunk を使わず、壊れた JSON を直接持つ iTXt チャンクを手で作る。
    itxt_data = b"gakei\x00" + bytes([0, 0]) + b"\x00" + b"\x00" + b"{not valid json"
    chunk = struct.pack(">I", len(itxt_data)) + b"iTXt" + itxt_data
    crc = zlib.crc32(b"iTXt" + itxt_data) & 0xFFFFFFFF
    chunk += struct.pack(">I", crc)

    iend_index = data.index(b"IEND") - 4
    malformed = data[:iend_index] + chunk + data[iend_index:]
    assert read_gakei_meta(malformed) is None


def test_read_returns_none_when_schema_mismatches() -> None:
    data = make_png_bytes()
    embedded = embed_gakei_chunk(data, {"schema": "something.else/1", "foo": "bar"})
    assert read_gakei_meta(embedded) is None


# -- build_lineage_meta -----------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def test_get_instance_id_is_stable_across_calls(db_session_factory: sessionmaker) -> None:
    with db_session_factory() as session:
        first = get_instance_id(session)
        second = get_instance_id(session)
        assert first == second

    with db_session_factory() as new_session:
        # 新しいセッション(= 再起動を模す)でも同じ id が読める(永続化されている)。
        assert get_instance_id(new_session) == first


def _node(meta: dict, node_id) -> dict:
    matches = [n for n in meta["nodes"] if n["id"] == str(node_id)]
    assert len(matches) == 1, (node_id, meta["nodes"])
    return matches[0]


def _edge(meta: dict, source, target, kind: str) -> dict:
    matches = [
        e
        for e in meta["edges"]
        if e["source"] == str(source) and e["target"] == str(target) and e["kind"] == kind
    ]
    assert len(matches) == 1, (source, target, kind, meta["edges"])
    return matches[0]


def test_build_lineage_meta_for_uploaded_asset_is_a_single_node_graph(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session:
        asset = ingest(session, local_store, make_png_bytes(), AssetKind.UPLOAD)
        session.commit()

        meta = build_lineage_meta(session, asset, viewer=LOCAL_ADMIN)
        assert meta["schema"] == "gakei.lineage/2"
        assert meta["root"] == str(asset.id)
        assert meta["truncated"] is False
        assert len(meta["nodes"]) == 1
        assert meta["edges"] == []
        node = _node(meta, asset.id)
        assert node["type"] == "asset"
        assert node["instance"] == get_instance_id(session)
        assert node["kind"] == "upload"
        assert node["sha256"] == asset.sha256
        assert node["mime"] == asset.mime
        assert node["width"] == asset.width
        assert node["height"] == asset.height


def test_build_lineage_meta_for_generated_asset_includes_run_and_inputs_and_excludes_comfyui_keys(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session:
        input_data = make_png_bytes(color=(1, 2, 3))
        input_asset = ingest(session, local_store, input_data, AssetKind.UPLOAD)
        session.flush()

        run = Run(
            provider="openai",
            model="gpt-image-2.5-sunburst",
            operation=RunOperation.EDIT,
            prompt="テスト用プロンプト",
            params={
                "size": "1024x1024",
                "comfyui_prompt": {"huge": "graph"},
                "comfyui_outputs": ["node1"],
            },
            status=RunStatus.SUCCEEDED,
            finished_at=_utcnow(),
        )
        session.add(run)
        session.flush()
        session.add(
            RunInput(run_id=run.id, asset_id=input_asset.id, role=RunInputRole.IMAGE, position=0)
        )
        session.flush()

        output_asset = ingest(
            session,
            local_store,
            make_png_bytes(color=(4, 5, 6)),
            AssetKind.GENERATED,
            produced_by_run_id=run.id,
            output_index=0,
        )
        session.commit()

        meta = build_lineage_meta(session, output_asset, viewer=LOCAL_ADMIN)
        run_node = _node(meta, run.id)
        assert run_node["type"] == "run"
        assert run_node["prompt"] == "テスト用プロンプト"
        assert run_node["model"] == "gpt-image-2.5-sunburst"
        assert run_node["status"] == "succeeded"
        assert "comfyui_prompt" not in run_node["params"]
        assert "comfyui_outputs" not in run_node["params"]
        assert run_node["params"]["size"] == "1024x1024"

        input_node = _node(meta, input_asset.id)
        assert input_node["sha256"] == input_asset.sha256

        output_edge = _edge(meta, run.id, output_asset.id, "output")
        assert output_edge["output_index"] == 0
        input_edge = _edge(meta, input_asset.id, run.id, "input")
        assert input_edge["role"] == "image"
        assert input_edge["position"] == 0


def test_build_lineage_meta_for_sketch_asset_includes_source_asset(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session:
        base = ingest(session, local_store, make_png_bytes(), AssetKind.UPLOAD)
        session.flush()
        sketch_data = make_png_bytes(color=(9, 9, 9))
        sketch = ingest(
            session, local_store, sketch_data, AssetKind.SKETCH, source_asset_id=base.id
        )
        session.commit()

        meta = build_lineage_meta(session, sketch, viewer=LOCAL_ADMIN)
        _node(meta, base.id)
        _edge(meta, base.id, sketch.id, "sketch_source")
        assert not any(n["type"] == "run" for n in meta["nodes"])


def test_build_lineage_meta_is_deterministic_for_same_asset(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session:
        asset = ingest(session, local_store, make_png_bytes(), AssetKind.UPLOAD)
        session.commit()

        first = build_lineage_meta(session, asset, viewer=LOCAL_ADMIN)
        second = build_lineage_meta(session, asset, viewer=LOCAL_ADMIN)
        assert first == second

        first_text = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        second_text = json.dumps(second, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert first_text == second_text


def _make_run(
    prompt: str, operation: RunOperation = RunOperation.GENERATE, params: dict | None = None
) -> Run:
    return Run(
        provider="openai",
        model="gpt-image-2.5-sunburst",
        operation=operation,
        prompt=prompt,
        params=params or {},
        status=RunStatus.SUCCEEDED,
        finished_at=_utcnow(),
    )


def test_build_lineage_meta_three_generation_chain_with_mask_and_reference_includes_all_ancestors(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    with db_session_factory() as session:
        gen_run = _make_run("generate root", params={"n": 1})
        session.add(gen_run)
        session.flush()
        asset_a = ingest(
            session,
            local_store,
            make_png_bytes(color=(1, 1, 1)),
            AssetKind.GENERATED,
            produced_by_run_id=gen_run.id,
            output_index=0,
        )
        session.flush()

        mask_asset = ingest(session, local_store, make_png_bytes(color=(2, 2, 2)), AssetKind.MASK)
        session.flush()
        edit1_run = _make_run("edit with mask", operation=RunOperation.EDIT)
        session.add(edit1_run)
        session.flush()
        session.add_all(
            [
                RunInput(
                    run_id=edit1_run.id, asset_id=asset_a.id, role=RunInputRole.IMAGE, position=0
                ),
                RunInput(
                    run_id=edit1_run.id, asset_id=mask_asset.id, role=RunInputRole.MASK, position=0
                ),
            ]
        )
        session.flush()
        asset_b = ingest(
            session,
            local_store,
            make_png_bytes(color=(3, 3, 3)),
            AssetKind.GENERATED,
            produced_by_run_id=edit1_run.id,
            output_index=0,
        )
        session.flush()

        reference_asset = ingest(
            session, local_store, make_png_bytes(color=(4, 4, 4)), AssetKind.UPLOAD
        )
        session.flush()
        edit2_run = _make_run("edit with reference", operation=RunOperation.EDIT)
        session.add(edit2_run)
        session.flush()
        session.add_all(
            [
                RunInput(
                    run_id=edit2_run.id, asset_id=asset_b.id, role=RunInputRole.IMAGE, position=0
                ),
                RunInput(
                    run_id=edit2_run.id,
                    asset_id=reference_asset.id,
                    role=RunInputRole.REFERENCE,
                    position=1,
                ),
            ]
        )
        session.flush()
        asset_c = ingest(
            session,
            local_store,
            make_png_bytes(color=(5, 5, 5)),
            AssetKind.GENERATED,
            produced_by_run_id=edit2_run.id,
            output_index=0,
        )
        session.commit()

        meta = build_lineage_meta(session, asset_c, viewer=LOCAL_ADMIN)
        assert meta["truncated"] is False

        node_ids = {n["id"] for n in meta["nodes"]}
        expected_ids = {
            str(x)
            for x in (
                asset_c.id,
                edit2_run.id,
                asset_b.id,
                reference_asset.id,
                edit1_run.id,
                asset_a.id,
                mask_asset.id,
                gen_run.id,
            )
        }
        assert expected_ids <= node_ids

        assert _node(meta, edit2_run.id)["prompt"] == "edit with reference"
        assert _node(meta, edit1_run.id)["model"] == "gpt-image-2.5-sunburst"
        assert _node(meta, gen_run.id)["operation"] == "generate"

        _edge(meta, asset_b.id, edit2_run.id, "input")
        _edge(meta, reference_asset.id, edit2_run.id, "input")
        _edge(meta, asset_a.id, edit1_run.id, "input")
        _edge(meta, mask_asset.id, edit1_run.id, "input")
        _edge(meta, edit2_run.id, asset_c.id, "output")
        _edge(meta, edit1_run.id, asset_b.id, "output")
        _edge(meta, gen_run.id, asset_a.id, "output")

        reference_edge = _edge(meta, reference_asset.id, edit2_run.id, "input")
        assert reference_edge["role"] == "reference"
        assert reference_edge["position"] == 1
        mask_edge = _edge(meta, mask_asset.id, edit1_run.id, "input")
        assert mask_edge["role"] == "mask"


def test_build_lineage_meta_caps_at_max_nodes_and_sets_truncated(
    db_session_factory: sessionmaker, local_store: LocalFsStore, monkeypatch: object
) -> None:
    import app.domain.embedded_meta as embedded_meta_module

    monkeypatch.setattr(embedded_meta_module, "MAX_LINEAGE_NODES", 3)

    with db_session_factory() as session:
        gen_run = _make_run("generate root")
        session.add(gen_run)
        session.flush()
        asset_a = ingest(
            session,
            local_store,
            make_png_bytes(color=(1, 1, 1)),
            AssetKind.GENERATED,
            produced_by_run_id=gen_run.id,
            output_index=0,
        )
        session.flush()
        edit_run = _make_run("edit", operation=RunOperation.EDIT)
        session.add(edit_run)
        session.flush()
        session.add(
            RunInput(run_id=edit_run.id, asset_id=asset_a.id, role=RunInputRole.IMAGE, position=0)
        )
        session.flush()
        asset_b = ingest(
            session,
            local_store,
            make_png_bytes(color=(2, 2, 2)),
            AssetKind.GENERATED,
            produced_by_run_id=edit_run.id,
            output_index=0,
        )
        session.commit()

        meta = build_lineage_meta(session, asset_b, viewer=LOCAL_ADMIN)
        assert meta["truncated"] is True
        assert len(meta["nodes"]) <= 3


def test_build_lineage_meta_size_cap_drops_params_then_prompt_from_farthest_ancestor(
    db_session_factory: sessionmaker, local_store: LocalFsStore, monkeypatch: object
) -> None:
    import app.domain.embedded_meta as embedded_meta_module

    monkeypatch.setattr(embedded_meta_module, "MAX_LINEAGE_BYTES", 2000)

    with db_session_factory() as session:
        big_params = {"note": "x" * 400}
        gen_run = _make_run("far ancestor prompt " + "y" * 200, params=big_params)
        session.add(gen_run)
        session.flush()
        asset_a = ingest(
            session,
            local_store,
            make_png_bytes(color=(1, 1, 1)),
            AssetKind.GENERATED,
            produced_by_run_id=gen_run.id,
            output_index=0,
        )
        session.flush()
        edit_run = _make_run("near ancestor prompt", operation=RunOperation.EDIT)
        session.add(edit_run)
        session.flush()
        session.add(
            RunInput(run_id=edit_run.id, asset_id=asset_a.id, role=RunInputRole.IMAGE, position=0)
        )
        session.flush()
        asset_b = ingest(
            session,
            local_store,
            make_png_bytes(color=(2, 2, 2)),
            AssetKind.GENERATED,
            produced_by_run_id=edit_run.id,
            output_index=0,
        )
        session.commit()

        meta = build_lineage_meta(session, asset_b, viewer=LOCAL_ADMIN)
        assert meta["truncated"] is True
        gen_node = _node(meta, gen_run.id)
        edit_node = _node(meta, edit_run.id)
        # 遠い祖先(gen_run)から先に params が落ちる。近い祖先(edit_run)はまだ残る。
        assert "params" not in gen_node
        assert "params" in edit_node


def test_build_lineage_meta_merges_embedded_graph_from_foreign_instance(tmp_path) -> None:
    """instance A で3世代(generate→edit)作った Asset を、instance B に取り込んで
    さらに edit したとき、B での埋め込みが A のノード(instance=A のまま)を
    origin エッジでつなげて含むこと(ADR-0014 6章)。
    """
    from app.db import create_all, make_engine, make_session_factory

    def _new_env(name: str) -> tuple[sessionmaker, LocalFsStore]:
        base = tmp_path / name
        # ADR-0027 6章: PostgreSQL で走らせているときは、インスタンスごとに別の一時 DB。
        engine = make_engine(extra_database_url() or (base / "gakei.db"))
        create_all(engine)
        return make_session_factory(engine), LocalFsStore(base)

    factory_a, store_a = _new_env("instance_a")
    factory_b, store_b = _new_env("instance_b")

    with factory_a() as session_a:
        instance_a = get_instance_id(session_a)
        gen_run = _make_run("A: generate")
        session_a.add(gen_run)
        session_a.flush()
        asset_a1 = ingest(
            session_a,
            store_a,
            make_png_bytes(color=(1, 1, 1)),
            AssetKind.GENERATED,
            produced_by_run_id=gen_run.id,
            output_index=0,
        )
        session_a.flush()
        edit_run = _make_run("A: edit", operation=RunOperation.EDIT)
        session_a.add(edit_run)
        session_a.flush()
        session_a.add(
            RunInput(run_id=edit_run.id, asset_id=asset_a1.id, role=RunInputRole.IMAGE, position=0)
        )
        session_a.flush()
        asset_a2 = ingest(
            session_a,
            store_a,
            make_png_bytes(color=(2, 2, 2)),
            AssetKind.GENERATED,
            produced_by_run_id=edit_run.id,
            output_index=0,
        )
        session_a.commit()

        meta_a = build_lineage_meta(session_a, asset_a2, viewer=LOCAL_ADMIN)

    with factory_b() as session_b:
        instance_b = get_instance_id(session_b)
        uploaded = ingest(session_b, store_b, make_png_bytes(color=(9, 9, 9)), AssetKind.UPLOAD)
        uploaded.origin_meta = meta_a
        session_b.flush()

        edit_run_b = _make_run("B: edit", operation=RunOperation.EDIT)
        session_b.add(edit_run_b)
        session_b.flush()
        session_b.add(
            RunInput(
                run_id=edit_run_b.id, asset_id=uploaded.id, role=RunInputRole.IMAGE, position=0
            )
        )
        session_b.flush()
        output_b = ingest(
            session_b,
            store_b,
            make_png_bytes(color=(8, 8, 8)),
            AssetKind.GENERATED,
            produced_by_run_id=edit_run_b.id,
            output_index=0,
        )
        session_b.commit()

        meta_b = build_lineage_meta(session_b, output_b, viewer=LOCAL_ADMIN)

    nodes_by_id = {n["id"]: n for n in meta_b["nodes"]}
    assert nodes_by_id[str(output_b.id)]["instance"] == instance_b
    assert nodes_by_id[str(edit_run_b.id)]["instance"] == instance_b
    assert nodes_by_id[str(uploaded.id)]["instance"] == instance_b

    a_root_id = meta_a["root"]
    assert a_root_id in nodes_by_id
    assert nodes_by_id[a_root_id]["instance"] == instance_a
    for a_node in meta_a["nodes"]:
        assert nodes_by_id[a_node["id"]]["instance"] == instance_a

    origin_edges = [
        e for e in meta_b["edges"] if e["kind"] == "origin" and e["target"] == str(uploaded.id)
    ]
    assert any(e["source"] == a_root_id for e in origin_edges)


# -- normalize_lineage_meta --------------------------------------------------


def _v1_asset_id() -> str:
    return str(uuid.uuid4())


def test_normalize_v1_converts_asset_run_inputs_to_v2_graph_shape() -> None:
    root_id = _v1_asset_id()
    run_id = _v1_asset_id()
    input_id = _v1_asset_id()
    meta = {
        "schema": SCHEMA_V1,
        "instance": "11111111-1111-1111-1111-111111111111",
        "asset": {
            "id": root_id,
            "sha256": "a" * 64,
            "kind": "generated",
            "output_index": 0,
            "created_at": "2026-09-24T00:00:00+00:00",
        },
        "run": {
            "id": run_id,
            "provider": "openai",
            "model": "gpt-image-2.5-sunburst",
            "operation": "edit",
            "prompt": "hello",
            "params": {"size": "1024x1024"},
            "finished_at": "2026-09-24T00:00:00+00:00",
        },
        "inputs": [{"asset_id": input_id, "sha256": "b" * 64, "role": "image", "position": 0}],
    }

    normalized = normalize_lineage_meta(meta)
    assert normalized is not None
    assert normalized["schema"] == SCHEMA_V2
    assert normalized["root"] == root_id
    assert normalized["instance"] == meta["instance"]

    nodes_by_id = {n["id"]: n for n in normalized["nodes"]}
    assert nodes_by_id[root_id]["type"] == "asset"
    assert nodes_by_id[root_id]["sha256"] == "a" * 64
    assert nodes_by_id[root_id]["width"] is None  # v1 は width/height/mime を持たない
    assert nodes_by_id[run_id]["type"] == "run"
    assert nodes_by_id[run_id]["status"] == "succeeded"  # v1 に status は無いので既定値
    assert nodes_by_id[input_id]["sha256"] == "b" * 64
    assert nodes_by_id[input_id]["kind"] is None  # v1 の入力は sha256 しか分からない

    output_edges = [e for e in normalized["edges"] if e["kind"] == "output"]
    assert any(e["source"] == run_id and e["target"] == root_id for e in output_edges)
    input_edges = [e for e in normalized["edges"] if e["kind"] == "input"]
    assert any(e["source"] == input_id and e["target"] == run_id for e in input_edges)


def test_normalize_v1_with_source_asset_produces_sketch_source_edge() -> None:
    root_id = _v1_asset_id()
    source_id = _v1_asset_id()
    meta = {
        "schema": SCHEMA_V1,
        "instance": "11111111-1111-1111-1111-111111111111",
        "asset": {
            "id": root_id,
            "sha256": "a" * 64,
            "kind": "sketch",
            "output_index": None,
            "created_at": "2026-09-24T00:00:00+00:00",
        },
        "source_asset": {"id": source_id, "sha256": "c" * 64},
    }
    normalized = normalize_lineage_meta(meta)
    assert normalized is not None
    edge = next(e for e in normalized["edges"] if e["kind"] == "sketch_source")
    assert edge["source"] == source_id
    assert edge["target"] == root_id


def test_normalize_v2_passes_through_valid_graph() -> None:
    root_id = _v1_asset_id()
    run_id = _v1_asset_id()
    meta = {
        "schema": SCHEMA_V2,
        "instance": "22222222-2222-2222-2222-222222222222",
        "root": root_id,
        "nodes": [
            {
                "type": "asset",
                "id": root_id,
                "instance": "22222222-2222-2222-2222-222222222222",
                "sha256": "a" * 64,
                "kind": "generated",
                "mime": "image/png",
                "width": 1024,
                "height": 1024,
                "created_at": "2026-09-24T00:00:00+00:00",
            },
            {
                "type": "run",
                "id": run_id,
                "instance": "22222222-2222-2222-2222-222222222222",
                "provider": "openai",
                "model": "m",
                "operation": "generate",
                "prompt": "p",
                "params": {},
                "status": "succeeded",
                "finished_at": None,
            },
        ],
        "edges": [{"source": run_id, "target": root_id, "kind": "output", "output_index": 0}],
        "truncated": False,
    }
    normalized = normalize_lineage_meta(meta)
    assert normalized == meta


def test_normalize_v2_drops_nodes_with_invalid_uuid() -> None:
    root_id = _v1_asset_id()
    meta = {
        "schema": SCHEMA_V2,
        "instance": "x",
        "root": root_id,
        "nodes": [
            {"type": "asset", "id": root_id, "sha256": "a" * 64},
            {"type": "asset", "id": "not-a-uuid", "sha256": "b" * 64},
        ],
        "edges": [],
        "truncated": False,
    }
    normalized = normalize_lineage_meta(meta)
    assert normalized is not None
    assert len(normalized["nodes"]) == 1
    assert normalized["nodes"][0]["id"] == root_id


def test_normalize_v2_drops_dangling_edges_and_unknown_kinds() -> None:
    root_id = _v1_asset_id()
    ghost_id = _v1_asset_id()
    meta = {
        "schema": SCHEMA_V2,
        "instance": "x",
        "root": root_id,
        "nodes": [{"type": "asset", "id": root_id, "sha256": "a" * 64}],
        "edges": [
            # target(ghost_id) が nodes に存在しない: 落ちる。
            {"source": root_id, "target": ghost_id, "kind": "origin"},
            # 知らない kind: 落ちる。
            {"source": root_id, "target": root_id, "kind": "teleport"},
        ],
        "truncated": False,
    }
    normalized = normalize_lineage_meta(meta)
    assert normalized is not None
    assert normalized["edges"] == []


def test_normalize_returns_none_for_non_dict_or_unknown_schema() -> None:
    assert normalize_lineage_meta(None) is None
    assert normalize_lineage_meta("not a dict") is None  # type: ignore[arg-type]
    assert normalize_lineage_meta({"schema": "something.else/1"}) is None
    assert normalize_lineage_meta({"schema": SCHEMA_V2, "root": "not-a-uuid", "nodes": []}) is None


def test_normalize_v2_caps_at_max_nodes_and_sets_truncated() -> None:
    root_id = _v1_asset_id()
    nodes = [{"type": "asset", "id": root_id, "sha256": "a" * 64}]
    nodes += [
        {"type": "asset", "id": str(uuid.uuid4()), "sha256": "b" * 64}
        for _ in range(MAX_LINEAGE_NODES + 50)
    ]
    meta = {
        "schema": SCHEMA_V2,
        "instance": "x",
        "root": root_id,
        "nodes": nodes,
        "edges": [],
        "truncated": False,
    }
    normalized = normalize_lineage_meta(meta)
    assert normalized is not None
    assert len(normalized["nodes"]) <= MAX_LINEAGE_NODES
    assert normalized["truncated"] is True


def test_normalize_v2_invalid_run_status_and_operation_fall_back() -> None:
    root_id = _v1_asset_id()
    run_id = _v1_asset_id()
    meta = {
        "schema": SCHEMA_V2,
        "instance": "x",
        "root": root_id,
        "nodes": [
            {"type": "asset", "id": root_id, "sha256": "a" * 64},
            {
                "type": "run",
                "id": run_id,
                "status": "not-a-real-status",
                "operation": "not-a-real-operation",
            },
        ],
        "edges": [],
        "truncated": False,
    }
    normalized = normalize_lineage_meta(meta)
    assert normalized is not None
    run_node = next(n for n in normalized["nodes"] if n["id"] == run_id)
    assert run_node["status"] == "succeeded"
    # 正規化は operation の値を検証しない(表示側の lineage.py が既定値にフォールバックする)。
    assert run_node["operation"] == "not-a-real-operation"


def test_downloaded_v1_style_png_is_still_normalizable_after_read(
    db_session_factory: sessionmaker, local_store: LocalFsStore
) -> None:
    """v1 を埋め込んだ PNG(旧バージョンの GAKEI からのダウンロードを模す)を
    read_gakei_meta で読み、normalize_lineage_meta で v2 のグラフ形に変換できること。
    """
    with db_session_factory() as session:
        asset = ingest(session, local_store, make_png_bytes(), AssetKind.UPLOAD)
        session.commit()

        v1_meta = {
            "schema": SCHEMA_V1,
            "instance": get_instance_id(session),
            "asset": {
                "id": str(asset.id),
                "sha256": asset.sha256,
                "kind": "upload",
                "output_index": None,
                "created_at": asset.created_at.isoformat(),
            },
        }
    embedded = embed_gakei_chunk(make_png_bytes(), v1_meta)
    read_back = read_gakei_meta(embedded)
    assert read_back == v1_meta

    normalized = normalize_lineage_meta(read_back)
    assert normalized is not None
    assert normalized["schema"] == SCHEMA_V2
    assert normalized["root"] == str(asset.id)

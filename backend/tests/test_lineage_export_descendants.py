"""ADR-0037 1章・4章: 系列の書き出しの範囲「この画像と子孫」(`descendants`)。

- 範囲: 起点と、子の方向にたどって現れる Asset だけ(祖先と、範囲の外の入力は含めない)。枝分かれ、
  見える範囲(ADR-0025)、論理削除、確認(preview)の数。
- 起点を生んだ Run は入れず、起点の `produced_by_run_id` と `output_index` は null。
- 取り込み: 作った Run が無い生成画像の起点はアップロードとして取り込む(Run をこしらえない)。
  起点以外や、範囲が `descendants` 以外では、Run の無い生成画像を断る。
- 納品用: 範囲の表示、起点を図の一番上に置くこと、作った実行が範囲の外であることの表示。
- 共有リンクの範囲には足さない(`descendants` は 422)。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.lineage_delivery import layout_graph
from app.domain.models import Asset, AssetKind, Run, RunInput, RunInputRole
from tests.conftest import login_as, make_png_bytes
from tests.test_lineage_export_import import (
    _counts,
    _entries,
    _export,
    _import,
    _import_ok,
    _manifest,
    _out,
    _rezip,
    _run,
    _second_client,
    _upload,
)


def _tree(client: TestClient) -> dict[str, dict]:
    """r0 → a0 → r1 → a1 → (r2 → a2、rb → b、rc(a1 と範囲の外のアップロード) → c)。"""
    r0 = _run(client, "root")
    r1 = _run(client, "middle", [_out(r0)])
    r2 = _run(client, "child", [_out(r1)])
    rb = _run(client, "branch", [_out(r1)])
    other = _upload(client, make_png_bytes(color=(7, 8, 9)))
    rc = _run(client, "with other", [_out(r1), other])
    return {"r0": r0, "r1": r1, "r2": r2, "rb": rb, "rc": rc, "other": {"id": other}}


def test_descendants_scope_contents(client: TestClient) -> None:
    t = _tree(client)
    a0, a1 = _out(t["r0"]), _out(t["r1"])
    manifest = _manifest(_export(client, a1, "descendants"))
    assert manifest["scope"] == "descendants"
    assert manifest["root_asset_id"] == a1
    assert {a["id"] for a in manifest["assets"]} == {
        a1,
        _out(t["r2"]),
        _out(t["rb"]),
        _out(t["rc"]),
    }
    assert {r["id"] for r in manifest["runs"]} == {t["r2"]["id"], t["rb"]["id"], t["rc"]["id"]}

    # 起点は生成画像のまま、作った Run(祖先の側)は入れず ID も出さない。
    root = next(a for a in manifest["assets"] if a["id"] == a1)
    assert root["kind"] == "generated"
    assert root["produced_by_run_id"] is None
    assert root["output_index"] is None
    text = json.dumps(manifest)
    assert t["r1"]["id"] not in text
    assert t["r0"]["id"] not in text
    assert a0 not in text
    assert t["other"]["id"] not in text

    # 子孫の出力は、作った Run とのつながりを保つ。範囲の外の入力は数だけ。
    child = next(a for a in manifest["assets"] if a["id"] == _out(t["r2"]))
    assert child["produced_by_run_id"] == t["r2"]["id"]
    assert child["output_index"] == 0
    rc = next(r for r in manifest["runs"] if r["id"] == t["rc"]["id"])
    assert rc["inputs"] == [{"asset_id": a1, "role": "image", "position": 0}]
    assert rc["omitted_input_count"] == 1

    # 葉から書き出すと起点だけ(Run は無い)。
    leaf = _manifest(_export(client, _out(t["r2"]), "descendants"))
    assert [a["id"] for a in leaf["assets"]] == [_out(t["r2"])]
    assert leaf["runs"] == []

    # 系列の根から書き出すと、祖先が無いので系列全体と同じ Asset になる。
    from_top = _manifest(_export(client, a0, "descendants"))
    whole = _manifest(_export(client, a0, "lineage"))
    assert {a["id"] for a in from_top["assets"]} == {a["id"] for a in whole["assets"]}
    assert {r["id"] for r in from_top["runs"]} == {r["id"] for r in whole["runs"]} - {t["r0"]["id"]}


def test_descendants_preview_counts(client: TestClient) -> None:
    t = _tree(client)
    preview = client.get(
        f"/api/assets/{_out(t['r1'])}/export/preview", params={"scope": "descendants"}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["scope"] == "descendants"
    assert body["asset_count"] == 4
    assert body["run_count"] == 3
    assert body["truncated"] is False


def test_descendants_excludes_deleted_assets(client: TestClient) -> None:
    t = _tree(client)
    assert client.delete(f"/api/assets/{_out(t['rb'])}").status_code == 204
    manifest = _manifest(_export(client, _out(t["r1"]), "descendants"))
    ids = {a["id"] for a in manifest["assets"]}
    assert _out(t["rb"]) not in ids
    assert t["rb"]["id"] not in {r["id"] for r in manifest["runs"]}
    assert ids == {_out(t["r1"]), _out(t["r2"]), _out(t["rc"])}


def test_descendants_respects_visibility(client_oidc: TestClient) -> None:
    login_as(client_oidc, "b@example.com", name="B")
    b_run = _run(client_oidc, "b's own")

    login_as(client_oidc, "a@example.com", name="Alice")
    r0 = _run(client_oidc, "root")
    r1 = _run(client_oidc, "child", [_out(r0)])
    # 以前のデータのように、B の Run が A の画像を入力にしていた(ADR-0025 4章)。
    with client_oidc.app.state.session_factory() as db:
        db.add(
            RunInput(
                run_id=uuid.UUID(b_run["id"]),
                asset_id=uuid.UUID(_out(r0)),
                role=RunInputRole.IMAGE,
                position=0,
            )
        )
        db.commit()

    manifest = _manifest(_export(client_oidc, _out(r0), "descendants"))
    assert {a["id"] for a in manifest["assets"]} == {_out(r0), _out(r1)}
    assert [r["id"] for r in manifest["runs"]] == [r1["id"]]
    text = json.dumps(manifest)
    assert b_run["id"] not in text
    assert _out(b_run) not in text


# -- 取り込み ----------------------------------------------------------------------


def test_descendants_round_trip_turns_runless_root_into_upload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    t = _tree(client)
    zip_bytes = _export(client, _out(t["r1"]), "descendants")
    source_root_sha = next(
        a["sha256"] for a in _manifest(zip_bytes)["assets"] if a["id"] == _out(t["r1"])
    )

    with _second_client(monkeypatch, tmp_path, "descendants-instance") as other:
        result = _import_ok(other, zip_bytes)
        assert result["created_asset_count"] == 4
        assert result["created_run_count"] == 3
        root_id = result["root_asset_id"]
        with other.app.state.session_factory() as db:
            root = db.get(Asset, uuid.UUID(root_id))
            assert root is not None
            # 作った Run が分からない画像は、アップロードとして取り込む(Run はこしらえない)。
            assert root.kind == AssetKind.UPLOAD
            assert root.produced_by_run_id is None
            assert root.output_index is None
            assert root.sha256 == source_root_sha
            assert len(db.execute(select(Run)).scalars().all()) == 3
            generated = db.execute(select(Asset).where(Asset.kind == AssetKind.GENERATED)).scalars()
            assert all(a.produced_by_run_id is not None for a in generated)

        lineage = other.get(f"/api/assets/{root_id}/lineage").json()
        assert len([n for n in lineage["nodes"] if n["type"] == "run"]) == 3
        assert len([n for n in lineage["nodes"] if n["type"] == "asset"]) == 4
        # 起点に親は無い(起点が終点の辺が無い)。
        assert not [e for e in lineage["edges"] if e["target"] == root_id]

        # もう一度取り込んでも何も増えない。
        before = _counts(other)
        again = _import_ok(other, zip_bytes)
        assert again["root_asset_id"] == root_id
        assert again["created_asset_count"] == 0
        assert again["created_run_count"] == 0
        assert _counts(other) == before

        # 取り込んだ起点から続きの Edit ができる。
        continued = _run(other, "continue", [root_id])
        assert continued["status"] == "succeeded"


def test_descendants_round_trip_with_upload_root(client: TestClient) -> None:
    upload_id = _upload(client, make_png_bytes(color=(30, 60, 90)))
    r1 = _run(client, "edit", [upload_id])
    manifest = _manifest(_export(client, upload_id, "descendants"))
    root = next(a for a in manifest["assets"] if a["id"] == upload_id)
    assert root["kind"] == "upload"
    assert [r["id"] for r in manifest["runs"]] == [r1["id"]]
    # 同じ利用者が取り込むと、起点は同じ内容の既存のアップロードを使う。Run は取り込みの記録
    # (`run_import`)が無いので新しく作る。
    result = _import_ok(client, _export(client, upload_id, "descendants"))
    assert result["root_asset_id"] == upload_id
    assert result["created_run_count"] == 1


@pytest.mark.parametrize("variant", ["not_root", "other_scope", "with_output_index"])
def test_runless_generated_asset_is_rejected_outside_descendants_root(
    client: TestClient, variant: str
) -> None:
    t = _tree(client)
    zip_bytes = _export(client, _out(t["r1"]), "descendants")
    entries = _entries(zip_bytes)
    manifest = _manifest(zip_bytes)
    if variant == "not_root":
        child = next(a for a in manifest["assets"] if a["id"] == _out(t["r2"]))
        child["produced_by_run_id"] = None
        child["output_index"] = None
        manifest["runs"] = [r for r in manifest["runs"] if r["id"] != t["r2"]["id"]]
    elif variant == "other_scope":
        manifest["scope"] = "lineage"
    else:
        root = next(a for a in manifest["assets"] if a["id"] == _out(t["r1"]))
        root["output_index"] = 0
    entries["manifest.json"] = json.dumps(manifest).encode()
    before = _counts(client)
    response = _import(client, _rezip(entries))
    assert response.status_code == 422, response.text
    assert "manifest.json" in response.json()["detail"]
    assert _counts(client) == before


# -- 納品用 ----------------------------------------------------------------------


def test_delivery_shows_descendants_scope_and_root_on_top(client: TestClient) -> None:
    t = _tree(client)
    root_id = _out(t["r1"])
    zip_bytes = _export(client, root_id, "descendants", mode="delivery", lang="ja")
    html_text = _entries(zip_bytes)["index.html"].decode("utf-8")
    assert "この画像と子孫" in html_text
    assert "この ZIP には含めていません(書き出しの範囲の外)" in html_text
    assert t["r1"]["id"] not in html_text
    assert f'id="asset-{root_id}"' in html_text
    assert "card asset-card is-root" in html_text

    layout = layout_graph(_manifest(zip_bytes))
    by_ident = {n.ident: n for n in layout.nodes}
    assert by_ident[root_id].layer == 0
    assert all(n.layer > 0 for n in layout.nodes if n.ident != root_id)
    assert all(by_ident[root_id].y < n.y for n in layout.nodes if n.ident != root_id)

    en = _entries(_export(client, root_id, "descendants", mode="delivery", lang="en"))
    assert "This image and its descendants" in en["index.html"].decode("utf-8")


# -- 共有リンクには足さない ----------------------------------------------------------


def test_share_scopes_do_not_accept_descendants(client: TestClient) -> None:
    assert client.patch("/api/settings/share", json={"enabled": True}).status_code == 200
    r0 = _run(client, "root")
    body = {"asset_id": _out(r0), "scope": "descendants"}
    assert client.post("/api/shares", json=body).status_code == 422
    assert client.post("/api/shares/preview", json=body).status_code == 422
    assert client.post("/api/shares", json={**body, "scope": "lineage"}).status_code == 201

"""ADR-0037 4章: 納品用の書き出し(`index.html` と `README.txt` を加えた ZIP)と、実行者の名前の選択。

- ZIP の中身(manifest、原本、index.html、README.txt)と、ファイル名。
- index.html: すべての画像の sha256、ZIP の中にある画像だけを相対パスで参照すること、外部の
  資源を読まないこと(リポジトリへのリンクだけ)、利用者のデータのエスケープ、ComfyUI の秘密に
  見える値を伏せること、取り込んだ記録の印、実行者の名前の有無、言語。
- README.txt: 日英の併記とリポジトリの URL。
- 納品用の ZIP も GAKEI に取り込めること。取り込み用(既定)には文書が入らないこと。
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.lineage_delivery import REPOSITORY_URL, layout_graph
from app.domain.models import Run
from tests.conftest import login_as
from tests.test_lineage_export_import import (
    _chain,
    _counts,
    _entries,
    _export,
    _import_ok,
    _manifest,
    _out,
    _run,
    _second_client,
)

pytestmark = pytest.mark.windows


def _delivery(client: TestClient, asset_id: str, scope: str = "ancestors", **params: str) -> bytes:
    return _export(client, asset_id, scope, mode="delivery", **params)


class _Collector(HTMLParser):
    """タグと属性、`id`、テキストを集める(壊れた HTML は `HTMLParser` が例外にしないので、
    開き・閉じの数もあわせて数える)。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.ids: set[str] = set()
        self.text: list[str] = []
        self.open_counts: dict[str, int] = {}
        self.close_counts: dict[str, int] = {}
        self._in_pre = False
        self.pre_texts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        self.tags.append((tag, attr))
        if attr.get("id"):
            self.ids.add(str(attr["id"]))
        self.open_counts[tag] = self.open_counts.get(tag, 0) + 1
        if tag == "pre":
            self._in_pre = True
            self.pre_texts.append("")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        self.tags.append((tag, attr))

    def handle_endtag(self, tag: str) -> None:
        self.close_counts[tag] = self.close_counts.get(tag, 0) + 1
        if tag == "pre":
            self._in_pre = False

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._in_pre:
            self.pre_texts[-1] += data


def _parse(html_text: str) -> _Collector:
    parser = _Collector()
    parser.feed(html_text)
    parser.close()
    return parser


def _html(zip_bytes: bytes) -> str:
    return _entries(zip_bytes)["index.html"].decode("utf-8")


def _readme(zip_bytes: bytes) -> str:
    raw = _entries(zip_bytes)["README.txt"]
    assert raw.startswith(b"\xef\xbb\xbf")
    return raw.decode("utf-8-sig")


def _branching_lineage(client: TestClient) -> tuple[dict, dict, dict, dict]:
    """generate → edit → edit と、1段目からの枝分かれの edit。"""
    r0, r1, r2 = _chain(client)
    branch = _run(client, "branch", [_out(r1)])
    return r0, r1, r2, branch


# -- ZIP の中身 ----------------------------------------------------------------------


def test_delivery_zip_contains_four_parts(client: TestClient) -> None:
    r0, r1, r2, branch = _branching_lineage(client)
    response = client.get(
        f"/api/assets/{_out(r1)}/export", params={"scope": "lineage", "mode": "delivery"}
    )
    assert response.status_code == 200, response.text
    assert 'filename="gakei-delivery-' in response.headers["content-disposition"]
    entries = _entries(response.content)
    manifest = json.loads(entries["manifest.json"])
    assert {"manifest.json", "index.html", "README.txt"} <= set(entries)
    asset_files = {a["file"] for a in manifest["assets"]}
    assert {n for n in entries if n.startswith("assets/")} == asset_files
    assert len(asset_files) == 4
    assert len(manifest["runs"]) == 4


def test_import_mode_default_has_no_documents(client: TestClient) -> None:
    r0 = _run(client, "root")
    response = client.get(f"/api/assets/{_out(r0)}/export")
    assert 'filename="gakei-lineage-' in response.headers["content-disposition"]
    names = set(_entries(response.content))
    assert "index.html" not in names
    assert "README.txt" not in names


def test_index_html_lists_everything_offline(client: TestClient) -> None:
    r0, r1, r2, branch = _branching_lineage(client)
    zip_bytes = _delivery(client, _out(r1), "lineage")
    entries = _entries(zip_bytes)
    manifest = json.loads(entries["manifest.json"])
    html_text = entries["index.html"].decode("utf-8")
    parsed = _parse(html_text)

    # 大まかな整形式(主要な要素の開きと閉じが対応する)。
    for tag in ("html", "head", "body", "main", "svg", "article", "dl", "section"):
        assert parsed.open_counts.get(tag, 0) == parsed.close_counts.get(tag, 0), tag
    assert parsed.open_counts["svg"] == 1
    assert html_text.startswith("<!doctype html>")

    # すべての画像の sha256 と、画像・実行ごとの節。
    for asset in manifest["assets"]:
        assert asset["sha256"] in html_text
        assert f"asset-{asset['id']}" in parsed.ids
    for run in manifest["runs"]:
        assert f"run-{run['id']}" in parsed.ids
        assert run["prompt"] in html_text

    # 画像は ZIP の中の相対パスだけ。ページ内リンクは存在する id を指す。
    image_refs = [a.get("src") for tag, a in parsed.tags if tag == "img"] + [
        a.get("href") for tag, a in parsed.tags if tag == "image"
    ]
    assert image_refs
    for ref in image_refs:
        assert ref in entries, ref
    for _tag, attrs in parsed.tags:
        href = attrs.get("href")
        if href and href.startswith("#"):
            assert href[1:] in parsed.ids, href

    # 外部の資源を読まない。外へのリンクはリポジトリだけ。JS も使わない。
    for tag, attrs in parsed.tags:
        for name in ("src", "href", "xlink:href", "action", "srcset"):
            value = attrs.get(name)
            if value and "://" in value:
                assert value == REPOSITORY_URL and tag == "a", (tag, name, value)
    assert "<script" not in html_text.lower()
    assert "<link" not in html_text.lower()
    assert "@import" not in html_text
    assert "url(http" not in html_text.replace(" ", "")

    # 主画像の印と、範囲・版・改ざんの検出が無いことの説明。
    assert "主画像" in html_text
    assert 'lang="ja"' in html_text
    assert "系列全体" in html_text
    assert manifest["gakei_version"] in html_text
    assert "改ざん" in html_text


def test_index_html_escapes_hostile_prompt(client: TestClient) -> None:
    hostile = '</pre><script>alert("x")</script> & <img src=x onerror=1> "q" \'s'
    r0 = _run(client, hostile)
    html_text = _html(_delivery(client, _out(r0)))
    assert "<script" not in html_text.lower()
    assert "<img src=x" not in html_text
    assert "&lt;/pre&gt;&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp;" in html_text
    parsed = _parse(html_text)
    assert hostile in parsed.pre_texts
    assert not any(attrs.get("onerror") for _tag, attrs in parsed.tags)


def test_index_html_escapes_hostile_params_and_model(client: TestClient) -> None:
    r0 = _run(client, "root")
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(r0["id"]))
        assert run is not None
        run.model = 'm"><script>x</script>'
        run.params = {'k<b>"': "<i>v</i>", "nested": {"a": "</pre><script>y</script>"}}
        db.commit()
    html_text = _html(_delivery(client, _out(r0)))
    assert "<script" not in html_text.lower()
    assert "<b>" not in html_text and "<i>v" not in html_text
    assert "&lt;i&gt;v&lt;/i&gt;" in html_text


def test_index_html_redacts_comfyui_secrets(client: TestClient) -> None:
    r0 = _run(client, "root")
    graph = {"12": {"class_type": "SomeApiNode", "inputs": {"api_key": "plain-secret", "n": 1}}}
    with client.app.state.session_factory() as db:
        run = db.get(Run, uuid.UUID(r0["id"]))
        assert run is not None
        run.provider = "comfyui"
        run.params = {"comfyui_prompt": graph}
        db.commit()
    zip_bytes = _delivery(client, _out(r0))
    for name, data in _entries(zip_bytes).items():
        assert b"plain-secret" not in data, name
    assert "***" in _html(zip_bytes)


# -- 実行者の名前と取り込んだ記録 ------------------------------------------------------------


def test_creator_names_are_omitted_by_default(client_oidc: TestClient) -> None:
    login_as(client_oidc, "a@example.com", name="Alice Example")
    r0 = _run(client_oidc, "root")
    r1 = _run(client_oidc, "child", [_out(r0)])

    zip_bytes = _delivery(client_oidc, _out(r1))
    for name, data in _entries(zip_bytes).items():
        assert b"Alice Example" not in data, name
        assert b"a@example.com" not in data, name
    assert all(r["created_by_name"] is None for r in _manifest(zip_bytes)["runs"])

    named = _delivery(client_oidc, _out(r1), include_creator_names="true")
    assert "Alice Example" in _html(named)
    assert all(r["created_by_name"] == "Alice Example" for r in _manifest(named)["runs"])
    for name, data in _entries(named).items():
        assert b"a@example.com" not in data, name


def test_imported_runs_are_flagged(client_oidc: TestClient) -> None:
    login_as(client_oidc, "a@example.com", name="Alice Example")
    r0 = _run(client_oidc, "root")
    source_zip = _export(client_oidc, _out(r0), include_creator_names="true")

    login_as(client_oidc, "b@example.com", name="Bob Example")
    root = _import_ok(client_oidc, source_zip)["root_asset_id"]
    continued = _run(client_oidc, "continue", [root])

    zip_bytes = _delivery(client_oidc, _out(continued))
    html_text = _html(zip_bytes)
    assert html_text.count("badge badge-imported") == 1
    assert "検証されていません" in html_text
    # 名前を含めないときは、元の実行者の名前も出さない。
    for name, data in _entries(zip_bytes).items():
        assert b"Alice Example" not in data, name
        assert b"Bob Example" not in data, name

    named = _html(_delivery(client_oidc, _out(continued), include_creator_names="true"))
    assert "Alice Example" in named
    assert "Bob Example" in named
    assert "元の実行者" in named


# -- 言語と README ----------------------------------------------------------------------


def test_language_follows_lang_param_then_accept_language(client: TestClient) -> None:
    r0 = _run(client, "root")
    ja = _html(_delivery(client, _out(r0)))
    assert 'lang="ja"' in ja and "画像の系列の記録" in ja

    en = _html(_delivery(client, _out(r0), lang="en"))
    assert 'lang="en"' in en and "Image lineage record" in en
    assert "画像の系列の記録" not in en

    response = client.get(
        f"/api/assets/{_out(r0)}/export",
        params={"mode": "delivery"},
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    assert 'lang="en"' in _html(response.content)

    # `lang` は Accept-Language より優先する(ダウンロードのリンクは画面の言語を送れないため)。
    response = client.get(
        f"/api/assets/{_out(r0)}/export",
        params={"mode": "delivery", "lang": "ja"},
        headers={"Accept-Language": "en"},
    )
    assert 'lang="ja"' in _html(response.content)


def test_timezone_param_changes_displayed_time(client: TestClient) -> None:
    r0 = _run(client, "root")
    tokyo = _html(_delivery(client, _out(r0), tz="Asia/Tokyo"))
    assert " JST<" in tokyo
    fallback = _html(_delivery(client, _out(r0), tz="Not/AZone"))
    assert " UTC<" in fallback


def test_readme_is_bilingual(client: TestClient) -> None:
    r0 = _run(client, "root")
    for lang in ("ja", "en"):
        readme = _readme(_delivery(client, _out(r0), lang=lang))
        assert REPOSITORY_URL in readme
        assert "セルフホスト" in readme and "self-hosted" in readme
        for name in ("index.html", "assets/", "manifest.json"):
            assert name in readme
        assert "\r\n" in readme


# -- 取り込める ----------------------------------------------------------------------


def test_delivery_zip_is_importable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    r0, r1, r2, branch = _branching_lineage(client)
    zip_bytes = _delivery(client, _out(r1), "lineage", lang="en")
    with _second_client(monkeypatch, tmp_path, "delivery-target") as other:
        assert _counts(other) == (0, 0, 0, 0)
        result = _import_ok(other, zip_bytes)
        assert result["created_asset_count"] == 4
        assert result["created_run_count"] == 4
        continued = _run(other, "continue", [result["root_asset_id"]])
        assert continued["status"] == "succeeded"


def test_delivery_zip_is_importable_by_another_user(client_oidc: TestClient) -> None:
    login_as(client_oidc, "a@example.com", name="Alice")
    r0, r1, r2 = _chain(client_oidc)
    zip_bytes = _delivery(client_oidc, _out(r2))
    login_as(client_oidc, "b@example.com", name="Bob")
    result = _import_ok(client_oidc, zip_bytes)
    assert result["created_run_count"] == 3
    run = client_oidc.get(f"/api/assets/{result['root_asset_id']}").json()["produced_by_run"]
    detail = client_oidc.get(f"/api/runs/{run['id']}").json()
    # 名前を含めずに書き出したので、元の実行者は分からない。
    assert detail["imported"]["source_creator_name"] is None


# -- 文言のキー ----------------------------------------------------------------------


def test_delivery_message_keys_exist() -> None:
    """`lineage_delivery.py` の `_d("...")`(リテラル)と、種類・操作・状態・辺の部分木のキーが、
    日英の両方にあること(`test_i18n.py` は `_d` の中の `t(f"...")` を走査できないため)。"""
    import ast

    import app.domain.lineage_delivery as module
    from app.i18n import t, use_locale

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    keys = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_d"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert len(keys) > 30
    keys |= {f"kinds.{k}" for k in ("upload", "generated", "mask", "sketch")}
    keys |= {f"operations.{k}" for k in ("generate", "edit")}
    keys |= {f"statuses.{k}" for k in ("queued", "running", "succeeded", "failed", "canceled")}
    keys |= {f"scopes.{k}" for k in ("ancestors", "lineage")}
    keys |= {
        f"edges.{k}" for k in ("primary", "image", "mask", "reference", "output", "sketch_source")
    }
    for loc in ("ja", "en"):
        with use_locale(loc):
            for key in keys:
                t(f"lineageExport.delivery.{key}", n=1, count=1, assets=1, runs=1)


# -- 図の配置 ----------------------------------------------------------------------


def test_layout_graph_puts_ancestors_above() -> None:
    a0, a1, a2, mask = (str(uuid.uuid4()) for _ in range(4))
    r0, r1 = str(uuid.uuid4()), str(uuid.uuid4())
    manifest = {
        "assets": [
            {"id": a0, "produced_by_run_id": r0, "created_at": "2026-01-01T00:00:01+00:00"},
            {"id": a1, "produced_by_run_id": r1, "created_at": "2026-01-01T00:00:03+00:00"},
            {"id": mask, "created_at": "2026-01-01T00:00:02+00:00"},
            {"id": a2, "source_asset_id": a1, "created_at": "2026-01-01T00:00:04+00:00"},
        ],
        "runs": [
            {"id": r0, "inputs": [], "created_at": "2026-01-01T00:00:00+00:00"},
            {
                "id": r1,
                "inputs": [
                    {"asset_id": a0, "role": "image", "position": 0},
                    {"asset_id": mask, "role": "mask", "position": 0},
                ],
                "created_at": "2026-01-01T00:00:02+00:00",
            },
        ],
    }
    layout = layout_graph(manifest)
    layer = {n.ident: n.layer for n in layout.nodes}
    assert layer[r0] == 0
    assert layer[a0] == 1
    assert layer[r1] == 2
    assert layer[a1] == 3
    assert layer[a2] == 4
    y = {n.ident: n.y for n in layout.nodes}
    assert y[r0] < y[a0] < y[r1] < y[a1] < y[a2]
    labels = {(e.source, e.target): (e.label, e.primary) for e in layout.edges}
    assert labels[(f"a:{a0}", f"r:{r1}")] == ("image", True)
    assert labels[(f"a:{mask}", f"r:{r1}")] == ("mask", False)
    assert labels[(f"a:{a1}", f"a:{a2}")] == ("sketch_source", False)
    assert all(0 <= n.x <= layout.width for n in layout.nodes)


def test_zip_documents_are_compressed(client: TestClient) -> None:
    r0 = _run(client, "root")
    with zipfile.ZipFile(io.BytesIO(_delivery(client, _out(r0)))) as zf:
        assert zf.getinfo("index.html").compress_type == zipfile.ZIP_DEFLATED
        assert zf.getinfo("README.txt").compress_type == zipfile.ZIP_DEFLATED

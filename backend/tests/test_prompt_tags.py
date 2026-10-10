"""プロンプトをタグで編集する: 語彙、候補の API、画像のタグをプロンプトにする API(ADR-0039)。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.annotation import tag_vocabulary
from app.annotation.tag_vocabulary import (
    RECHECK_SECONDS,
    find_vocabulary_file,
    get_vocabulary,
    load_vocabulary,
)
from app.annotation.wd_models import TAGS_FILE, WD_MODELS, models_root
from app.domain import annotations as annotations_domain
from tests.conftest import login_as, make_png_bytes

pytestmark = pytest.mark.windows

CSV = (
    "tag_id,name,category,count\n"
    "1,general,9,900000\n"  # rating は語彙に入れない
    "2,1girl,0,500000\n"
    "3,smile,0,300000\n"
    "4,long_hair,0,400000\n"
    "5,hat_(object),0,1000\n"
    "6,short_hair,0,200000\n"
    "7,hatsune_miku,4,90000\n"
    "8,sky,0,150000\n"
    "9,artist_x,1,5000\n"  # artist(1)も入れない
)


@pytest.fixture(autouse=True)
def _fresh_vocabulary_cache() -> None:
    tag_vocabulary.clear_cache()
    yield
    tag_vocabulary.clear_cache()


def _write_vocabulary(data_dir: Path, text: str = CSV) -> Path:
    model = next(iter(WD_MODELS))
    directory = models_root(data_dir) / model
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / TAGS_FILE
    path.write_text(text, encoding="utf-8")
    return path


def _upload(client: TestClient, color=(10, 20, 30)) -> str:
    response = client.post(
        "/api/assets",
        files={"file": ("a.png", make_png_bytes(48, 32, color), "image/png")},
        data={"kind": "upload"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _set_auto_tags(client: TestClient, asset_id: str, tags: list[tuple[str, float | None]]) -> None:
    with client.app.state.session_factory() as db:
        annotations_domain.apply_auto_result(db, uuid.UUID(asset_id), title=None, tags=tags)
        db.commit()


# -- 語彙 ----------------------------------------------------------------------


def test_load_vocabulary_keeps_general_and_character(tmp_path: Path) -> None:
    path = tmp_path / TAGS_FILE
    path.write_text(CSV, encoding="utf-8")
    vocabulary = load_vocabulary(path)
    names = [e.name for e in vocabulary.entries]
    # count の多い順、`_` は空白、rating と artist は除く。
    assert names == [
        "1girl",
        "long hair",
        "smile",
        "short hair",
        "sky",
        "hatsune miku",
        "hat (object)",
    ]
    assert "hat (object)" in vocabulary
    assert "general" not in vocabulary


def test_find_vocabulary_file_and_absent(tmp_path: Path) -> None:
    assert find_vocabulary_file(tmp_path) is None
    assert get_vocabulary(tmp_path) is None
    path = _write_vocabulary(tmp_path)
    assert find_vocabulary_file(tmp_path) == path


def test_vocabulary_is_loaded_once_and_rechecked_when_absent(tmp_path: Path) -> None:
    assert get_vocabulary(tmp_path, now=0.0) is None
    _write_vocabulary(tmp_path)
    # 無かったときは間をおくまで見直さない。
    assert get_vocabulary(tmp_path, now=1.0) is None
    vocabulary = get_vocabulary(tmp_path, now=RECHECK_SECONDS + 1)
    assert vocabulary is not None and len(vocabulary) == 7
    # 読めた後は一度きり(ファイルを変えても読み直さない)。
    _write_vocabulary(tmp_path, "tag_id,name,category,count\n1,cat,0,1\n")
    assert get_vocabulary(tmp_path, now=RECHECK_SECONDS * 10) is vocabulary


# -- 候補の API -------------------------------------------------------------------


def test_suggestions_prefix_first_then_partial(client: TestClient, data_dir: Path) -> None:
    _write_vocabulary(data_dir)
    body = client.get("/api/tags/suggestions", params={"q": "ha"}).json()
    assert body["vocabulary_available"] is True
    names = [i["name"] for i in body["items"]]
    # 前方一致(count の多い順)→ 部分一致(count の多い順)
    assert names == ["hatsune miku", "hat (object)", "long hair", "short hair"]
    assert {i["source"] for i in body["items"]} == {"vocabulary"}


def test_suggestions_accept_underscore_and_escaped_parens(
    client: TestClient, data_dir: Path
) -> None:
    _write_vocabulary(data_dir)
    names = [
        i["name"]
        for i in client.get("/api/tags/suggestions", params={"q": "long_h"}).json()["items"]
    ]
    assert names == ["long hair"]
    names = [
        i["name"]
        for i in client.get("/api/tags/suggestions", params={"q": "hat \\(obj"}).json()["items"]
    ]
    assert names == ["hat (object)"]


def test_suggestions_merge_gakei_tags_after_vocabulary(client: TestClient, data_dir: Path) -> None:
    _write_vocabulary(data_dir)
    asset_id = _upload(client)
    for name in ("smile", "smiling cat", "スマイル", "big smile"):
        assert client.post(f"/api/assets/{asset_id}/tags", json={"name": name}).status_code == 200
    items = client.get("/api/tags/suggestions", params={"q": "smil"}).json()["items"]
    # 語彙の前方一致 → GAKEI の前方一致 → GAKEI の部分一致。日本語のタグは出さない。
    # 語彙にある smile は語彙として1回だけ。
    assert [(i["name"], i["source"]) for i in items] == [
        ("smile", "vocabulary"),
        ("smiling cat", "tag"),
        ("big smile", "tag"),
    ]


def test_suggestions_without_vocabulary_use_gakei_tags(client: TestClient) -> None:
    asset_id = _upload(client)
    client.post(f"/api/assets/{asset_id}/tags", json={"name": "red hat"})
    body = client.get("/api/tags/suggestions", params={"q": "hat"}).json()
    assert body["vocabulary_available"] is False
    assert [i["name"] for i in body["items"]] == ["red hat"]


def test_suggestions_empty_query_and_limit(client: TestClient, data_dir: Path) -> None:
    _write_vocabulary(data_dir)
    assert client.get("/api/tags/suggestions", params={"q": "  "}).json()["items"] == []
    items = client.get("/api/tags/suggestions", params={"q": "h", "limit": 2}).json()["items"]
    assert len(items) == 2
    assert client.get("/api/tags/suggestions", params={"q": "h", "limit": 21}).status_code == 422


def test_suggestions_only_own_assets_tags(client_oidc: TestClient) -> None:
    login_as(client_oidc, "alice@example.com")
    asset_id = _upload(client_oidc)
    client_oidc.post(f"/api/assets/{asset_id}/tags", json={"name": "secret hat"})
    client_oidc.post("/api/auth/logout")

    login_as(client_oidc, "bob@example.com")
    items = client_oidc.get("/api/tags/suggestions", params={"q": "hat"}).json()["items"]
    assert items == []
    # 画像のタグも他人の Asset は 404。
    assert client_oidc.get(f"/api/assets/{asset_id}/prompt-tags").status_code == 404


def test_suggestions_require_login(client_oidc: TestClient) -> None:
    assert client_oidc.get("/api/tags/suggestions", params={"q": "a"}).status_code == 401


# -- 画像のタグをプロンプトにする ------------------------------------------------------


def test_prompt_tags_order_and_removed(client: TestClient, data_dir: Path) -> None:
    _write_vocabulary(data_dir)
    asset_id = _upload(client)
    _set_auto_tags(
        client,
        asset_id,
        [("smile", 0.6), ("1girl", 0.95), ("hat (object)", 0.7), ("笑顔", 0.6), ("sky", 0.4)],
    )
    for name in ("long hair", "my original tag", "short hair"):
        client.post(f"/api/assets/{asset_id}/tags", json={"name": name})
    client.delete(f"/api/assets/{asset_id}/tags/sky")

    body = client.get(f"/api/assets/{asset_id}/prompt-tags").json()
    assert body["vocabulary_available"] is True
    # score の高い順 → 人が付けた順。語彙に無いもの(訳、独自のタグ)と removed は入らない。
    # 括弧はエスケープしない。
    assert body["tags"] == ["1girl", "hat (object)", "smile", "long hair", "short hair"]


def test_prompt_tags_without_vocabulary_use_ascii_tags(client: TestClient) -> None:
    asset_id = _upload(client)
    _set_auto_tags(client, asset_id, [("smile", 0.6), ("笑顔", 0.6), ("1girl", 0.9)])
    client.post(f"/api/assets/{asset_id}/tags", json={"name": "my tag"})
    body = client.get(f"/api/assets/{asset_id}/prompt-tags").json()
    assert body == {
        "asset_id": asset_id,
        "tags": ["1girl", "smile", "my tag"],
        "vocabulary_available": False,
    }


def test_prompt_tags_unknown_asset(client: TestClient) -> None:
    assert client.get(f"/api/assets/{uuid.uuid4()}/prompt-tags").status_code == 404

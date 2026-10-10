"""タグ辞書(ADR-0041)。登録(CSV・zip、種類の判定、上限)、管理、候補、訳、WD Tagger の訳。

辞書の中身はテストの中で作る架空の小さな CSV だけを使う(配布されている辞書は使わない)。
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.domain import tag_dictionaries as dictionaries
from app.domain.models import (
    AssetAnnotation,
    TagDictionaryAlias,
    TagDictionaryEntry,
    TagDictionaryTranslation,
)
from tests.test_annotation_language import RecordingEngines, _tag_names
from tests.test_annotations import _patch_settings, _upload, _wait_annotation

# CSV の文字コード(BOM、CRLF)、zip、SQLite への取り込みを含む(ADR-0012)。
pytestmark = pytest.mark.windows

# 架空のタグの一覧(4 列。件数は小数表記、別名はカンマ区切りで引用符)。
TAGS_CSV = (
    'long_hair,0,5000.0,"longhair,ロングヘア"\n'
    "long_sleeves,0,3000.0,\n"
    'looking_at_viewer,0,4000.0,"eye_contact"\n'
    "lolita_fashion,0,800.0,\n"
    'cat_ears,0,2500.0,"nekomimi,ネコミミ"\n'
    "cat,0,1200.0,\n"
    "^_^,0,100.0,\n"
    "test_artist,1,50.0,\n"
    'test_character_(series),4,700.0,"tchar"\n'
)
# 架空の訳(2 列。訳はカンマ区切りで先頭が代表。CRLF と BOM)。
TRANSLATIONS_CSV = (
    '﻿long_hair,"ロングヘアー,長髪"\r\n'
    'cat_ears,"猫耳,ねこみみ"\r\n'
    'cat,"猫,ねこ"\r\n'
    "long_sleeves,長袖\r\n"
    "unknown_tag,未知\r\n"
)


def _post(client: TestClient, filename: str, data: bytes, **fields: str):  # noqa: ANN202
    return client.post(
        "/api/settings/tag-dictionaries",
        files={"file": (filename, data, "application/octet-stream")},
        data=fields,
    )


def _wait(client: TestClient) -> None:
    assert client.app.state.tag_dictionary_importer.wait_idle(30)


def _register(client: TestClient, filename: str, text: str, **fields: str) -> list[dict]:
    response = _post(client, filename, text.encode("utf-8"), **fields)
    assert response.status_code == 202, response.text
    _wait(client)
    return response.json()["items"]


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _list(client: TestClient) -> list[dict]:
    response = client.get("/api/settings/tag-dictionaries")
    assert response.status_code == 200
    return response.json()["items"]


def _suggest(client: TestClient, q: str, **params: object) -> dict:
    response = client.get("/api/tags/suggestions", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def both(client: TestClient) -> TestClient:
    _register(client, "tags.csv", TAGS_CSV)
    _register(client, "translations.csv", TRANSLATIONS_CSV)
    return client


# -- 登録 ------------------------------------------------------------------------


def test_register_tags_and_translations(client: TestClient) -> None:
    response = _post(client, "tags.csv", TAGS_CSV.encode())
    assert response.status_code == 202, response.text
    created = response.json()["items"]
    assert [(i["filename"], i["kind"], i["status"]) for i in created] == [
        ("tags.csv", "tags", "importing")
    ]
    assert created[0]["category_scheme"] == "danbooru"
    _wait(client)
    _register(client, "tr.csv", TRANSLATIONS_CSV)

    items = _list(client)
    assert [(i["kind"], i["status"], i["row_count"], i["enabled"]) for i in items] == [
        ("tags", "ready", 9, True),
        ("translations", "ready", 5, True),
    ]
    assert items[1]["category_scheme"] is None
    assert items[0]["finished_at"] is not None
    assert client.get("/api/settings/tag-dictionaries").json()["category_schemes"] == [
        "danbooru",
        "other",
    ]


def test_register_keeps_names_and_parses_counts(client: TestClient) -> None:
    _register(client, "tags.csv", TAGS_CSV)
    with client.app.state.session_factory() as db:
        entry = db.scalars(
            select(TagDictionaryEntry).where(TagDictionaryEntry.name == "long_hair")
        ).one()
        assert (entry.name_key, entry.category, entry.post_count) == ("long_hair", 0, 5000)
        aliases = {a.alias for a in db.scalars(select(TagDictionaryAlias))}
        assert aliases == {"longhair", "ロングヘア", "eye_contact", "nekomimi", "ネコミミ", "tchar"}


def test_register_zip_with_both_kinds_ignores_other_files(client: TestClient) -> None:
    data = _zip(
        {
            "dict/tags.csv": TAGS_CSV.encode(),
            "dict/translations_jp.csv": TRANSLATIONS_CSV.encode("utf-8"),
            "dict/readme.txt": b"hello",
        }
    )
    response = _post(client, "pack.zip", data)
    assert response.status_code == 202, response.text
    names = [(i["filename"], i["kind"]) for i in response.json()["items"]]
    assert names == [
        ("pack.zip/tags.csv", "tags"),
        ("pack.zip/translations_jp.csv", "translations"),
    ]
    _wait(client)
    assert {i["status"] for i in _list(client)} == {"ready"}


def test_register_skips_unreadable_rows_and_accepts_three_columns(client: TestClient) -> None:
    lines = [f"tag_{i},0,{i}.0" for i in range(30)] + ["broken,row"]
    items = _register(client, "three.csv", "\n".join(lines) + "\n")
    assert items[0]["kind"] == "tags"
    assert _list(client)[0]["row_count"] == 30


def test_register_category_scheme(client: TestClient) -> None:
    items = _register(client, "e.csv", TAGS_CSV, category_scheme="other")
    assert items[0]["category_scheme"] == "other"
    response = _post(client, "e.csv", TAGS_CSV.encode(), category_scheme="nope")
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("filename", "data", "status"),
    [
        ("mixed.csv", b"a,b,c,d,e\nx\n1,2,3,4,5,6\n", 422),
        ("empty.csv", b"", 422),
        ("blank.csv", b"\n\n", 422),
        ("sjis.csv", "猫,ねこ\n".encode("shift_jis"), 422),
        ("bad.zip", b"PK\x03\x04garbage", 422),
        ("none.zip", _zip({"readme.txt": b"x"}), 422),
    ],
)
def test_register_rejects_bad_files(
    client: TestClient, filename: str, data: bytes, status: int
) -> None:
    response = _post(client, filename, data)
    assert response.status_code == status, response.text
    assert response.json()["detail"]
    assert _list(client) == []


def test_register_rejects_mostly_undetermined_rows(client: TestClient) -> None:
    good = [f"tag_{i},0,{i}.0," for i in range(8)]
    bad = [f"x{i}" for i in range(4)]
    response = _post(client, "half.csv", ("\n".join(good + bad) + "\n").encode())
    assert response.status_code == 422


def test_register_rejects_zip_over_extracted_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dictionaries, "EXTRACTED_MAX_BYTES", 1000)
    # 圧縮すると小さいが、展開すると上限を超える(zip 爆弾の形)。
    data = _zip({"big.csv": ("a,b\n" * 1000).encode()})
    assert len(data) < 1000
    response = _post(client, "bomb.zip", data)
    assert response.status_code == 413


def test_register_rejects_zip_with_too_many_files(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dictionaries, "ZIP_CSV_MAX", 2)
    data = _zip({f"{i}.csv": TRANSLATIONS_CSV.encode() for i in range(3)})
    assert _post(client, "many.zip", data).status_code == 422


def test_zip_member_names_are_not_used_as_paths(client: TestClient, data_dir: Path) -> None:
    data = _zip({"../../evil.csv": TRANSLATIONS_CSV.encode()})
    response = _post(client, "x.zip", data)
    assert response.status_code == 202
    assert response.json()["items"][0]["filename"] == "x.zip/evil.csv"
    _wait(client)
    assert not (data_dir.parent / "evil.csv").exists()


def test_register_requires_multipart_file(client: TestClient) -> None:
    response = client.post("/api/settings/tag-dictionaries", json={})
    assert response.status_code == 422
    response = client.post(
        "/api/settings/tag-dictionaries", files={"other": ("a.csv", b"a,b", "text/csv")}
    )
    assert response.status_code == 422


def test_register_rejects_too_large(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dictionaries, "UPLOAD_MAX_BYTES", 100)
    response = _post(client, "big.csv", b"a,b\n" * 100)
    assert response.status_code == 413


def test_upload_is_not_written_to_disk(client: TestClient, data_dir: Path) -> None:
    before = {p for p in data_dir.rglob("*") if p.is_file()}
    _register(client, "tags.csv", TAGS_CSV)
    after = {p for p in data_dir.rglob("*") if p.is_file()}
    # DB(SQLite)のファイル以外は増えない。
    assert {p.name for p in after - before} <= {"gakei.db", "gakei.db-wal", "gakei.db-shm"}


# -- 管理 ------------------------------------------------------------------------


def test_patch_and_delete(both: TestClient) -> None:
    client = both
    tags_id = _list(client)[0]["id"]
    response = client.patch(f"/api/settings/tag-dictionaries/{tags_id}", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    # 無効にすると候補は従来の語彙と GAKEI のタグに戻る。
    assert _suggest(client, "long")["dictionary_available"] is False

    response = client.patch(
        f"/api/settings/tag-dictionaries/{tags_id}",
        json={"enabled": True, "category_scheme": "other"},
    )
    assert response.json()["category_scheme"] == "other"
    assert _suggest(client, "long")["items"][0]["category_scheme"] == "other"

    assert client.delete(f"/api/settings/tag-dictionaries/{tags_id}").status_code == 204
    assert [i["kind"] for i in _list(client)] == ["translations"]
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(TagDictionaryEntry)) == 0
        assert db.scalar(select(func.count()).select_from(TagDictionaryAlias)) == 0
        assert db.scalar(select(func.count()).select_from(TagDictionaryTranslation)) == 5

    missing = uuid.uuid4()
    assert client.delete(f"/api/settings/tag-dictionaries/{missing}").status_code == 404
    response = client.patch(f"/api/settings/tag-dictionaries/{missing}", json={"enabled": True})
    assert response.status_code == 404


def test_delete_while_importing_is_conflict(client: TestClient) -> None:
    with client.app.state.session_factory() as db:
        row = dictionaries.create_dictionary(
            db, filename="x.csv", kind="tags", category_scheme="danbooru", user_id=None
        )
        db.commit()
    assert client.delete(f"/api/settings/tag-dictionaries/{row.id}").status_code == 409


def test_recover_interrupted_marks_failed_and_removes_rows(client: TestClient) -> None:
    factory = client.app.state.session_factory
    with factory() as db:
        row = dictionaries.create_dictionary(
            db, filename="x.csv", kind="tags", category_scheme="danbooru", user_id=None
        )
        db.add(
            TagDictionaryEntry(
                dictionary_id=row.id, name_key="a", name="a", category=0, post_count=1
            )
        )
        db.commit()
        assert dictionaries.recover_interrupted(db) == 1
    items = _list(client)
    assert items[0]["status"] == "failed"
    assert items[0]["error_code"] == "interrupted"
    assert items[0]["error_message"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(TagDictionaryEntry)) == 0


def test_importing_dictionary_is_not_used(client: TestClient) -> None:
    with client.app.state.session_factory() as db:
        row = dictionaries.create_dictionary(
            db, filename="x.csv", kind="tags", category_scheme="danbooru", user_id=None
        )
        db.add(
            TagDictionaryEntry(
                dictionary_id=row.id, name_key="zzz_tag", name="zzz_tag", category=0, post_count=1
            )
        )
        db.commit()
    body = _suggest(client, "zzz")
    assert body["dictionary_available"] is False
    assert body["items"] == []


def test_import_failure_is_recorded(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker.tag_dictionary_importer import TagDictionaryImporter

    def broken(self, session, dictionary_id, text):  # noqa: ANN001, ANN202
        raise RuntimeError("boom")

    monkeypatch.setattr(TagDictionaryImporter, "_import_tags", broken)
    _register(client, "tags.csv", TAGS_CSV)
    item = _list(client)[0]
    assert (item["status"], item["error_code"]) == ("failed", "importFailed")


# -- 候補 ------------------------------------------------------------------------


def test_suggestions_by_name_prefix_ordered_by_count(both: TestClient) -> None:
    body = _suggest(both, "lo")
    assert body["dictionary_available"] is True
    names = [i["name"] for i in body["items"]]
    # タグ名の前方一致(件数の多い順)が先、続けて別名(longhair → long hair は既出なので重ねない)。
    assert names[:4] == ["long hair", "looking at viewer", "long sleeves", "lolita fashion"]
    first = body["items"][0]
    assert first == {
        "name": "long hair",
        "source": "dictionary",
        "count": 5000,
        "category": 0,
        "category_scheme": "danbooru",
        "translation": "ロングヘアー",
        "match": "name",
        "matched": None,
    }


def test_suggestions_accept_spaces_underscores_and_escapes(both: TestClient) -> None:
    assert _suggest(both, "long h")["items"][0]["name"] == "long hair"
    assert _suggest(both, "LONG_H")["items"][0]["name"] == "long hair"
    items = _suggest(both, "test character \\(s")["items"]
    assert [(i["name"], i["category"]) for i in items] == [("test character (series)", 4)]


def test_suggestions_by_alias(both: TestClient) -> None:
    items = _suggest(both, "neko")["items"]
    assert [(i["name"], i["match"], i["matched"]) for i in items] == [
        ("cat ears", "alias", "nekomimi")
    ]
    items = _suggest(both, "eye")["items"]
    assert (items[0]["name"], items[0]["matched"]) == ("looking at viewer", "eye_contact")


def test_suggestions_from_japanese_translation(both: TestClient) -> None:
    # 訳の前方一致(ねこ → cat、ねこみみ → cat ears)を件数の多い順に、続けて部分一致。
    items = _suggest(both, "ねこ")["items"]
    assert [(i["name"], i["match"], i["matched"]) for i in items] == [
        ("cat ears", "translation", "ねこみみ"),
        ("cat", "translation", "ねこ"),
    ]
    items = _suggest(both, "髪")["items"]
    assert [(i["name"], i["match"], i["matched"]) for i in items] == [
        ("long hair", "translation", "長髪")
    ]
    # 別名(カタカナ)の前方一致は訳より先。
    items = _suggest(both, "ネコ")["items"]
    assert items[0]["name"] == "cat ears"
    assert items[0]["match"] == "alias"


def test_suggestions_keep_kaomoji_and_limit(both: TestClient) -> None:
    assert _suggest(both, "^_")["items"][0]["name"] == "^_^"
    assert len(_suggest(both, "l", limit=2)["items"]) == 2
    assert _suggest(both, "")["items"] == []
    assert _suggest(both, "zzzz")["items"] == []


def test_suggestions_merge_dictionaries_keeping_higher_count(both: TestClient) -> None:
    _register(both, "more.csv", "long_hair,0,9000.0,\nlong_neck,0,10.0,\n")
    items = _suggest(both, "long")["items"]
    assert [(i["name"], i["count"]) for i in items] == [
        ("long hair", 9000),
        ("long sleeves", 3000),
        ("long neck", 10),
    ]


def test_suggestions_without_tag_list_add_translations_to_vocabulary(
    client: TestClient,
) -> None:
    _register(client, "tr.csv", TRANSLATIONS_CSV)
    body = _suggest(client, "long")
    assert body["dictionary_available"] is False
    for item in body["items"]:
        assert item["source"] in ("vocabulary", "tag")


# -- 訳 --------------------------------------------------------------------------


def test_translations_endpoint(both: TestClient) -> None:
    response = both.post(
        "/api/tags/translations",
        json={"names": ["long hair", "cat_ears", "Cat", "nothing", "", "long hair"]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "translations": {"long hair": "ロングヘアー", "cat_ears": "猫耳", "Cat": "猫"}
    }


def test_translations_endpoint_limits_names(both: TestClient) -> None:
    response = both.post("/api/tags/translations", json={"names": ["a"] * 201})
    assert response.status_code == 422


def test_translations_prefer_first_registered(both: TestClient) -> None:
    _register(both, "tr2.csv", "cat,にゃんこ\nsmile,笑顔\n")
    body = both.post("/api/tags/translations", json={"names": ["cat", "smile"]}).json()
    assert body["translations"] == {"cat": "猫", "smile": "笑顔"}


def test_translations_without_dictionary_is_empty(client: TestClient) -> None:
    body = client.post("/api/tags/translations", json={"names": ["cat"]}).json()
    assert body == {"translations": {}}


# -- WD Tagger の訳(ADR-0024 6章、ADR-0041 5章) -----------------------------------


@pytest.fixture
def recording(client: TestClient) -> RecordingEngines:
    engines = RecordingEngines(llm_delay=0.0, onnx_delay=0.0)
    client.app.state.annotator.engines = engines
    return engines


def test_annotation_uses_dictionary_translation_and_asks_llm_only_for_the_rest(
    client: TestClient, recording: RecordingEngines
) -> None:
    # ONNX のタグは long hair と candy。辞書には long hair の訳だけがある。
    _register(client, "tr.csv", TRANSLATIONS_CSV)
    _patch_settings(
        client, auto_on_ingest=True, llm_enabled=True, onnx_enabled=True, tag_language="localized"
    )
    body = _wait_annotation(client, _upload(client)["id"])
    assert body["annotation"]["status"] == "succeeded", body["annotation"]
    assert _tag_names(body) == {"long hair", "candy", "ロングヘアー", "訳 candy"}
    assert recording.translate_calls == [["candy"]]


def test_annotation_skips_llm_when_dictionary_covers_all_tags(
    client: TestClient, recording: RecordingEngines
) -> None:
    _register(client, "tr.csv", TRANSLATIONS_CSV + "candy,キャンディ\r\n")
    _patch_settings(
        client, auto_on_ingest=True, llm_enabled=True, onnx_enabled=True, tag_language="localized"
    )
    asset_id = _upload(client)["id"]
    body = _wait_annotation(client, asset_id)
    assert _tag_names(body) == {"long hair", "candy", "ロングヘアー", "キャンディ"}
    assert recording.translate_calls == []
    # LLM を呼んでいないので、記録するエンジンにも入れない。
    with client.app.state.session_factory() as db:
        row = db.get(AssetAnnotation, uuid.UUID(asset_id))
        assert row is not None and row.auto_engines == "onnx"
    assert client.get("/api/settings/annotation").json()["calls_last_hour"] == 0


def test_annotation_vlm_translates_only_tags_missing_from_dictionary(
    client: TestClient, recording: RecordingEngines
) -> None:
    _register(client, "tr.csv", TRANSLATIONS_CSV)
    _patch_settings(
        client,
        auto_on_ingest=True,
        vlm_enabled=True,
        onnx_enabled=True,
        tag_language="localized",
        language="ja",
    )
    body = _wait_annotation(client, _upload(client)["id"])
    # VLM には同じ意味のタグを付け直さないよう ONNX のタグをすべて渡し、訳は candy だけ頼む。
    assert recording.known_tags_seen == [["long hair", "candy"]]
    assert _tag_names(body) == {
        "long hair",
        "candy",
        "ロングヘアー",
        "訳 candy",
        "fake vlm",
        "landscape",
    }


def test_annotation_ignores_too_long_dictionary_translation(
    client: TestClient, recording: RecordingEngines
) -> None:
    _register(client, "tr.csv", "long_hair," + "長" * 150 + "\r\n")
    _patch_settings(
        client, auto_on_ingest=True, llm_enabled=True, onnx_enabled=True, tag_language="localized"
    )
    body = _wait_annotation(client, _upload(client)["id"])
    assert recording.translate_calls == [["long hair", "candy"]]
    assert "訳 long hair" in _tag_names(body)


def test_vlm_instructions_ask_only_for_subset() -> None:
    from app.annotation.engines import build_vlm_instructions
    from app.domain.annotation_settings import AnnotationConfig

    config = AnnotationConfig(tag_language="localized", language="ja")
    text = build_vlm_instructions(config, False, ["long hair", "candy"], ["candy"])
    assert "次のものの日本語訳" in text and "candy" in text
    text = build_vlm_instructions(config, False, ["long hair", "candy"], [])
    assert "translations" not in text
    full = build_vlm_instructions(config, False, ["long hair", "candy"], None)
    assert full == build_vlm_instructions(config, False, ["long hair", "candy"])


# -- 読み取りの単体 ----------------------------------------------------------------


def test_detect_kind_and_rows() -> None:
    assert dictionaries.detect_kind("a", TAGS_CSV) == "tags"
    assert dictionaries.detect_kind("b", TRANSLATIONS_CSV.lstrip("﻿")) == "translations"
    rows = list(dictionaries.iter_translation_rows(TRANSLATIONS_CSV))
    assert rows[0].translation == "ロングヘアー"
    assert rows[0].translations == "ロングヘアー, 長髪"
    assert dictionaries.translation_search_key(["ロングヘアー", "長髪"]) == ",ロングヘアー,長髪,"


def test_error_codes_have_messages() -> None:
    ja = json.loads((Path(__file__).parents[1] / "app/locales/ja.json").read_text("utf-8"))
    for code in (
        dictionaries.ERROR_INTERRUPTED,
        dictionaries.ERROR_IMPORT_FAILED,
        dictionaries.ERROR_NO_ROWS,
    ):
        assert ja["tagDictionaries"]["errors"][code]
    for code in (
        "tooLarge",
        "noFile",
        "invalidScheme",
        "notFound",
        "importing",
        "emptyFile",
        "invalidZip",
        "zipTooManyFiles",
        "zipNoCsv",
        "zipEncrypted",
        "extractedTooLarge",
        "invalidEncoding",
        "undetermined",
    ):
        assert ja["tagDictionaries"][code]

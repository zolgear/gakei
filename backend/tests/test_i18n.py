"""ADR-0015: サーバーの多言語化(`app.i18n`)。"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.i18n import console_t, parse_accept_language, t, use_locale

_APP_DIR = Path(__file__).resolve().parents[1] / "app"
_LOCALES_DIR = _APP_DIR / "locales"

# --- parse_accept_language -----------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "ja"),
        ("", "ja"),
        ("ja", "ja"),
        ("ja-JP", "ja"),
        ("en", "en"),
        ("en-US", "en"),
        ("fr", "ja"),  # 未対応言語は既定(日本語)
        ("fr-FR,de;q=0.8", "ja"),
        ("en;q=0.5, ja;q=0.9", "ja"),  # q 値が高い方
        ("ja;q=0.2, en;q=0.9", "en"),
        ("fr;q=1.0, en;q=0.9", "en"),  # 先頭が未対応でも、対応言語が見つかれば使う
        ("EN-us", "en"),  # 大文字小文字を無視
    ],
)
def test_parse_accept_language(header: str | None, expected: str) -> None:
    assert parse_accept_language(header) == expected


def test_use_locale_context_manager_restores_previous_value() -> None:
    from app.i18n import current_locale

    assert current_locale() == "ja"
    with use_locale("en"):
        assert current_locale() == "en"
    assert current_locale() == "ja"


# --- HTTPException detail は Accept-Language に従う -----------------------------


def test_http_exception_detail_is_japanese_by_default(client: TestClient) -> None:
    import uuid

    response = client.get(f"/api/assets/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "asset が見つかりません"


def test_http_exception_detail_is_english_with_accept_language(client: TestClient) -> None:
    import uuid

    response = client.get(f"/api/assets/{uuid.uuid4()}", headers={"Accept-Language": "en"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Asset not found"


# --- capabilities のラベルも言語ごとに変わる ------------------------------------


def test_capabilities_quality_labels_follow_accept_language(client: TestClient) -> None:
    ja = client.get("/api/capabilities").json()
    en = client.get("/api/capabilities", headers={"Accept-Language": "en"}).json()

    ja_params = ja["providers"][0]["models"][0]["operations"][0]["params"]
    en_params = en["providers"][0]["models"][0]["operations"][0]["params"]
    ja_quality = next(p for p in ja_params if p["name"] == "quality")
    en_quality = next(p for p in en_params if p["name"] == "quality")

    assert ja_quality["label"] == "画質"
    assert en_quality["label"] == "Quality"
    assert ja_quality["choice_labels"]["low"] == "低"
    assert en_quality["choice_labels"]["low"] == "Low"


# --- run-validation の 422 detail -----------------------------------------------


def test_run_validation_error_detail_follows_accept_language(client: TestClient) -> None:
    body = {
        "provider": "fake",
        "operation": "generate",
        "model": "does-not-exist",
        "prompt": "hello",
        "params": {},
        "inputs": [],
    }
    ja_response = client.post("/api/runs", json=body)
    en_response = client.post("/api/runs", json=body, headers={"Accept-Language": "en"})
    assert ja_response.status_code == 422
    assert en_response.status_code == 422
    assert ja_response.json()["detail"] == "未知のモデルです: does-not-exist"
    assert en_response.json()["detail"] == "Unknown model: does-not-exist"


# --- ComfyUI のワークフロー形式エラー -------------------------------------------


def test_comfyui_ui_format_message_follows_accept_language(client: TestClient) -> None:
    ui_format_template = {"nodes": [], "links": []}
    ja_response = client.post(
        "/api/comfyui/workflows/analyze", json={"template": ui_format_template}
    )
    en_response = client.post(
        "/api/comfyui/workflows/analyze",
        json={"template": ui_format_template},
        headers={"Accept-Language": "en"},
    )
    assert ja_response.status_code == 422
    assert en_response.status_code == 422
    assert "UI 形式" in ja_response.json()["detail"]
    assert "UI format" in en_response.json()["detail"]


# --- console_t(ランチャー) -----------------------------------------------------


def test_console_t_uses_lc_all_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")
    assert console_t("launcher.startupAborted", error="x") == "起動を中止しました: x"

    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    assert console_t("launcher.startupAborted", error="x") == "Startup aborted: x"


def test_console_t_falls_back_to_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    assert console_t("launcher.buildComplete") == "フロントのビルドが完了しました。"


def test_console_t_defaults_to_english_without_japanese_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert console_t("launcher.buildComplete") == "Frontend build complete."


# --- t() 自体の挙動 ----------------------------------------------------------


def test_t_formats_placeholders() -> None:
    with use_locale("ja"):
        assert t("provider.unknown", name="foo") == "未知のプロバイダーです: foo"
    with use_locale("en"):
        assert t("provider.unknown", name="foo") == "Unknown provider: foo"


def test_t_unknown_key_raises_key_error() -> None:
    with pytest.raises(KeyError):
        t("does.not.exist")


def test_t_pluralizes_with_count() -> None:
    with use_locale("en"):
        assert (
            t("runValidation.exactImageCount", count=1, current=0)
            == "This workflow requires exactly 1 input image (got 0)"
        )
        assert (
            t("runValidation.exactImageCount", count=2, current=0)
            == "This workflow requires exactly 2 input images (got 0)"
        )


def test_t_pluralized_key_without_count_raises_key_error() -> None:
    with pytest.raises(KeyError):
        t("runValidation.exactImageCount", current=0)


# --- ロケール JSON の整合性(ADR-0015 2. のテスト) ------------------------------


def _load(loc: str) -> dict:
    with (_LOCALES_DIR / f"{loc}.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def _flatten(node, prefix: str = "") -> dict[str, object]:
    """`{"one": ..., "other": ...}` は1つの葉(そのままのdict)として扱う。"""
    out: dict[str, object] = {}
    if isinstance(node, dict) and not (set(node.keys()) <= {"one", "other"} and node):
        for key, value in node.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            out.update(_flatten(value, child_prefix))
        return out
    out[prefix] = node
    return out


def _placeholder_names(value: str) -> set[str]:
    import string

    names = set()
    for _literal, field_name, _spec, _conv in string.Formatter().parse(value):
        if field_name:
            names.add(field_name)
    return names


def test_locale_files_have_the_same_keys() -> None:
    ja = _flatten(_load("ja"))
    en = _flatten(_load("en"))
    assert set(ja) == set(en)


def test_locale_files_have_matching_one_other_shapes() -> None:
    ja = _flatten(_load("ja"))
    en = _flatten(_load("en"))
    for key, ja_value in ja.items():
        en_value = en[key]
        assert isinstance(ja_value, dict) == isinstance(en_value, dict), key
        if isinstance(ja_value, dict):
            assert set(ja_value) == {"one", "other"}, key
            assert set(en_value) == {"one", "other"}, key


def test_locale_files_have_matching_placeholder_names() -> None:
    ja = _flatten(_load("ja"))
    en = _flatten(_load("en"))
    for key, ja_value in ja.items():
        en_value = en[key]
        if isinstance(ja_value, dict):
            for shape in ("one", "other"):
                assert _placeholder_names(ja_value[shape]) == _placeholder_names(en_value[shape]), (
                    f"{key}.{shape}"
                )
        else:
            assert _placeholder_names(ja_value) == _placeholder_names(en_value), key


_HIRAGANA_KATAKANA_KANJI = (
    "぀-ゟ"  # ひらがな
    "゠-ヿ"  # カタカナ
    "一-鿿"  # 漢字(CJK統合漢字)
    "　-〿"  # 全角句読点等(日本語の句読点・かぎ括弧)
)


def test_english_values_contain_no_japanese() -> None:
    import re

    pattern = re.compile(f"[{_HIRAGANA_KATAKANA_KANJI}]")
    en = _flatten(_load("en"))
    for key, value in en.items():
        if isinstance(value, dict):
            for shape, text in value.items():
                assert not pattern.search(text), f"{key}.{shape}: {text!r}"
        else:
            assert not pattern.search(value), f"{key}: {value!r}"


# --- コード中で使われているキーが両方の JSON にある ------------------------------


# `t(unavailable_hint_key)`(comfyui/client.py の `_send`)は例外。呼び出し元の
# `_send(..., unavailable_hint_key="comfyui.client.hint*")` はすべてリテラルで、
# ここに列挙したキーがその全候補(直接 grep で確認できる)。
_KNOWN_DYNAMIC_KEY_SITES: dict[str, frozenset[str]] = {
    "app/providers/comfyui/client.py": frozenset(
        {
            "comfyui.client.hintUpload",
            "comfyui.client.hintSubmit",
            "comfyui.client.hintFetchOutput",
            "comfyui.client.hintContact",
        }
    ),
}


def _literal_t_keys() -> set[str]:
    """`app/` 以下の `.py` を ast で走査し、`t("...")` / `console_t("...")` の
    最初の引数がリテラルであるものをすべて集める。"""
    keys: set[str] = set()
    non_literal: list[str] = []
    for path in _APP_DIR.rglob("*.py"):
        rel = path.relative_to(_APP_DIR.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("t", "console_t")
            ):
                if not node.args:
                    non_literal.append(f"{path}:{node.lineno} (no args)")
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
                elif rel in _KNOWN_DYNAMIC_KEY_SITES:
                    keys.update(_KNOWN_DYNAMIC_KEY_SITES[rel])
                else:
                    non_literal.append(f"{path}:{node.lineno}")
    assert not non_literal, f"t()/console_t() の最初の引数がリテラルではありません: {non_literal}"
    return keys


def test_every_key_used_in_code_exists_in_both_locales() -> None:
    ja = _flatten(_load("ja"))
    en = _flatten(_load("en"))
    used_keys = _literal_t_keys()
    missing_ja = used_keys - set(ja)
    missing_en = used_keys - set(en)
    assert not missing_ja, f"ja.json に無いキー: {missing_ja}"
    assert not missing_en, f"en.json に無いキー: {missing_en}"

"""タグの言語(native / localized)と推定の実行順(ADR-0024 6章)。

- ONNX を先に動かし、そのタグを VLM に渡す。LLM(タイトル)は ONNX と並行して動かす。
- localized(ja)では ONNX の英語のタグの訳を足す(VLM が有効なら同じ VLM の呼び出しで、
  無効で LLM が有効なら LLM で)。localized(en)と native では訳さない。

エンジンは FakeEngines を元にしたモックで、実 API もモデルも使わない。
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.annotation.engines import (
    AnnotationEngineError,
    EngineContext,
    FakeEngines,
    OpenAIEngines,
    VlmResult,
    build_vlm_instructions,
)
from app.annotation.wd_tagger import WdTagger
from app.domain.annotation_settings import AnnotationConfig, Connection
from app.domain.models import AssetTag, Tag
from app.worker.annotator import translated_tags
from tests.test_annotation_engines import _engines_with, _FakeClient
from tests.test_annotations import _generate, _patch_settings, _upload, _wait_annotation


class RecordingEngines(FakeEngines):
    """呼ばれた順と引数を記録する。LLM と ONNX には少し時間をかけ、重なりを確かめる。"""

    def __init__(self, llm_delay: float = 0.3, onnx_delay: float = 0.3) -> None:
        self.events: list[tuple[str, float]] = []
        self.known_tags_seen: list[list[str] | None] = []
        self.translate_calls: list[list[str]] = []
        self.vlm_calls = 0
        self.llm_delay = llm_delay
        self.onnx_delay = onnx_delay
        self._lock = threading.Lock()

    def _mark(self, name: str) -> None:
        with self._lock:
            self.events.append((name, time.monotonic()))

    def at(self, name: str) -> float:
        return next(t for n, t in self.events if n == name)

    async def title_from_prompt(self, prompt: str, ctx: EngineContext) -> str:
        self._mark("llm_start")
        await asyncio.sleep(self.llm_delay)
        self._mark("llm_end")
        return await super().title_from_prompt(prompt, ctx)

    def onnx_tags(self, image: Any, ctx: EngineContext) -> list[tuple[str, float]]:
        self._mark("onnx_start")
        time.sleep(self.onnx_delay)  # 別スレッドで動く(to_thread)
        self._mark("onnx_end")
        return [("long hair", 0.9), ("candy", 0.7)]

    async def describe_image(
        self,
        image_jpeg: bytes,
        prompt: str | None,
        want_title: bool,
        ctx: EngineContext,
        known_tags: list[str] | None = None,
    ) -> VlmResult:
        self._mark("vlm_start")
        self.vlm_calls += 1
        self.known_tags_seen.append(known_tags)
        return await super().describe_image(image_jpeg, prompt, want_title, ctx, known_tags)

    async def translate_tags(self, tags: list[str], ctx: EngineContext) -> dict[str, str]:
        self._mark("translate_start")
        self.translate_calls.append(tags)
        return await super().translate_tags(tags, ctx)


@pytest.fixture
def recording(client: TestClient) -> RecordingEngines:
    engines = RecordingEngines()
    client.app.state.annotator.engines = engines
    return engines


def _tag_names(body: dict) -> set[str]:
    return {t["name"] for t in body["tags"]}


# -- 実行順 --------------------------------------------------------------------


def test_llm_runs_in_parallel_with_onnx_and_vlm_after_onnx(
    client: TestClient, recording: RecordingEngines
) -> None:
    _patch_settings(
        client, auto_on_ingest=True, llm_enabled=True, vlm_enabled=True, onnx_enabled=True
    )
    run = _generate(client, "a girl with candy")
    body = _wait_annotation(client, run["outputs"][0]["asset_id"])
    assert body["annotation"]["status"] == "succeeded", body["annotation"]

    # LLM と ONNX は重なって動く(どちらも相手が終わる前に始まっている)。
    assert recording.at("llm_start") < recording.at("onnx_end")
    assert recording.at("onnx_start") < recording.at("llm_end")
    # VLM は ONNX の後。ONNX のタグを受け取る。
    assert recording.at("vlm_start") >= recording.at("onnx_end")
    assert recording.known_tags_seen == [["long hair", "candy"]]
    assert body["title"] == "ダミー: a girl with candy"


def test_title_failure_fails_whole_job_and_skips_vlm(client: TestClient) -> None:
    class BrokenTitle(RecordingEngines):
        async def title_from_prompt(self, prompt: str, ctx: EngineContext) -> str:
            raise AnnotationEngineError("LLM が壊れた")

    engines = BrokenTitle(onnx_delay=0.2)
    client.app.state.annotator.engines = engines
    _patch_settings(
        client, auto_on_ingest=True, llm_enabled=True, vlm_enabled=True, onnx_enabled=True
    )
    run = _generate(client, "a cat")
    body = _wait_annotation(client, run["outputs"][0]["asset_id"])
    assert body["annotation"]["status"] == "failed"
    assert body["annotation"]["error"] == "LLM が壊れた"
    assert engines.vlm_calls == 0
    assert body["tags"] == []


def test_onnx_failure_cancels_title_and_fails(client: TestClient) -> None:
    class BrokenOnnx(RecordingEngines):
        def onnx_tags(self, image: Any, ctx: EngineContext) -> list[tuple[str, float]]:
            raise AnnotationEngineError("ONNX が壊れた")

    engines = BrokenOnnx(llm_delay=5.0)
    client.app.state.annotator.engines = engines
    _patch_settings(client, auto_on_ingest=True, llm_enabled=True, onnx_enabled=True)
    run = _generate(client, "a cat")
    started = time.monotonic()
    body = _wait_annotation(client, run["outputs"][0]["asset_id"])
    assert body["annotation"]["status"] == "failed"
    assert body["annotation"]["error"] == "ONNX が壊れた"
    # タイトルの LLM(5 秒かかる)を待たずに終わる。
    assert time.monotonic() - started < 4.0
    assert body["title"] is None


# -- タグの言語 ------------------------------------------------------------------


def test_native_keeps_each_engine_language_without_translation(
    client: TestClient, recording: RecordingEngines
) -> None:
    _patch_settings(
        client, auto_on_ingest=True, vlm_enabled=True, onnx_enabled=True, tag_language="native"
    )
    body = _wait_annotation(client, _upload(client)["id"])
    assert body["annotation"]["status"] == "succeeded"
    assert _tag_names(body) == {"long hair", "candy", "fake vlm", "landscape"}
    # VLM には ONNX のタグを渡す(同じ意味のタグを付け直さない)。
    assert recording.known_tags_seen == [["long hair", "candy"]]
    assert recording.translate_calls == []


def test_localized_ja_adds_translations_from_the_same_vlm_call(
    client: TestClient, recording: RecordingEngines
) -> None:
    _patch_settings(
        client,
        auto_on_ingest=True,
        vlm_enabled=True,
        llm_enabled=True,
        onnx_enabled=True,
        tag_language="localized",
        language="ja",
    )
    asset_id = _upload(client)["id"]
    body = _wait_annotation(client, asset_id)
    assert _tag_names(body) == {
        "long hair",
        "candy",
        "訳 long hair",
        "訳 candy",
        "fake vlm",
        "landscape",
    }
    # 訳は VLM の呼び出しで返させ、LLM での翻訳は呼ばない(呼び出しを増やさない)。
    assert recording.vlm_calls == 1
    assert recording.translate_calls == []
    # 訳のタグは auto で、元の ONNX のタグの確信度を引き継ぐ。
    assert _scores(client, asset_id) == {
        "long hair": 0.9,
        "訳 long hair": 0.9,
        "candy": 0.7,
        "訳 candy": 0.7,
        "fake vlm": None,
        "landscape": None,
    }


def test_localized_en_does_not_translate(client: TestClient, recording: RecordingEngines) -> None:
    _patch_settings(
        client,
        auto_on_ingest=True,
        vlm_enabled=True,
        llm_enabled=True,
        onnx_enabled=True,
        tag_language="localized",
        language="en",
    )
    body = _wait_annotation(client, _upload(client)["id"])
    assert _tag_names(body) == {"long hair", "candy", "fake vlm", "landscape"}
    assert recording.known_tags_seen == [["long hair", "candy"]]
    assert recording.translate_calls == []


def test_localized_ja_without_vlm_translates_with_llm(
    client: TestClient, recording: RecordingEngines
) -> None:
    _patch_settings(
        client, auto_on_ingest=True, llm_enabled=True, onnx_enabled=True, tag_language="localized"
    )
    asset_id = _upload(client)["id"]  # プロンプトの無いアップロード(タイトルの LLM は呼ばない)
    body = _wait_annotation(client, asset_id)
    assert body["annotation"]["status"] == "succeeded"
    assert _tag_names(body) == {"long hair", "candy", "訳 long hair", "訳 candy"}
    assert recording.translate_calls == [["long hair", "candy"]]
    # 翻訳は ONNX の後。
    assert recording.at("translate_start") >= recording.at("onnx_end")
    # 翻訳の呼び出しも1時間の上限に数える。
    assert client.get("/api/settings/annotation").json()["calls_last_hour"] == 1


def test_localized_ja_without_llm_and_vlm_keeps_english_only(
    client: TestClient, recording: RecordingEngines
) -> None:
    _patch_settings(client, auto_on_ingest=True, onnx_enabled=True, tag_language="localized")
    body = _wait_annotation(client, _upload(client)["id"])
    assert _tag_names(body) == {"long hair", "candy"}
    assert recording.translate_calls == []
    assert recording.vlm_calls == 0


def test_tag_language_setting_roundtrip_and_validation(client: TestClient) -> None:
    assert client.get("/api/settings/annotation").json()["tag_language"] == "localized"
    assert _patch_settings(client, tag_language="native")["tag_language"] == "native"
    response = client.patch("/api/settings/annotation", json={"tag_language": "fr"})
    assert response.status_code == 422


def _scores(client: TestClient, asset_id: str) -> dict[str, float | None]:
    with client.app.state.session_factory() as db:
        rows = db.execute(
            Tag.__table__.select()
            .with_only_columns(Tag.name, AssetTag.score)
            .join(AssetTag, AssetTag.tag_id == Tag.id)
            .where(AssetTag.asset_id == uuid.UUID(asset_id))
        ).all()
    return {name: (round(score, 3) if score is not None else None) for name, score in rows}


def test_translated_tags_matches_keys_loosely() -> None:
    onnx = [("long hair", 0.9), ("candy", 0.7), ("smile", 0.5)]
    result = translated_tags(onnx, {"Long_Hair": "長髪", "candy": "キャンディ", "smile": "smile"})
    # 元と同じ表記の訳(smile)は足さない。
    assert result == [("長髪", 0.9), ("キャンディ", 0.7)]
    assert translated_tags(onnx, {}) == []


# -- VLM と LLM へのプロンプト ------------------------------------------------------


def test_vlm_instructions_native() -> None:
    config = AnnotationConfig(tag_language="native", language="ja")
    text = build_vlm_instructions(config, want_title=False, known_tags=["long hair", "candy"])
    assert "long hair, candy" in text
    assert "別の言語" in text
    # native ではタグの言語を指定せず、訳も頼まない。
    assert "日本語の短い名詞" not in text
    assert "translations" not in text


def test_vlm_instructions_localized_ja_asks_for_translations() -> None:
    config = AnnotationConfig(tag_language="localized", language="ja")
    text = build_vlm_instructions(config, want_title=True, known_tags=["long hair"])
    assert "日本語の短い名詞" in text
    assert "long hair" in text
    assert "translations" in text
    assert "引用符" in text  # タイトルに記号や引用符を付けない
    # ONNX のタグが無ければ訳は頼まない。
    assert "translations" not in build_vlm_instructions(config, want_title=False, known_tags=None)


def test_vlm_instructions_localized_en_does_not_translate() -> None:
    config = AnnotationConfig(tag_language="localized", language="en")
    text = build_vlm_instructions(config, want_title=False, known_tags=["long hair"])
    assert "short English noun" in text
    assert "long hair" in text
    assert "translations" not in text


def test_openai_vlm_receives_onnx_tags_and_returns_translations(tmp_path: Path) -> None:
    client = _FakeClient(
        '{"tags": ["お菓子の家"], "translations": {"candy": "キャンディ", "x": 1}}'
    )
    engines = _engines_with(client, tmp_path)
    ctx = EngineContext(AnnotationConfig(language="ja"), Connection("k", None))
    result = asyncio.run(engines.describe_image(b"jpeg", None, False, ctx, ["candy"]))
    assert result.tags == ["お菓子の家"]
    assert result.translations == {"candy": "キャンディ"}
    instructions = client.calls[0][1]["instructions"]
    assert "candy" in instructions and "translations" in instructions


def test_openai_vlm_drops_translations_when_native(tmp_path: Path) -> None:
    client = _FakeClient('{"tags": ["cat"], "translations": {"candy": "キャンディ"}}')
    engines = _engines_with(client, tmp_path)
    ctx = EngineContext(AnnotationConfig(tag_language="native"), Connection("k", None))
    result = asyncio.run(engines.describe_image(b"jpeg", None, False, ctx, ["candy"]))
    assert result.translations == {}


def test_openai_translate_tags_uses_llm_model(tmp_path: Path) -> None:
    client = _FakeClient('```json\n{"translations": {"candy": "キャンディ"}}\n```')
    engines = _engines_with(client, tmp_path)
    ctx = EngineContext(AnnotationConfig(llm_model="llm-x"), Connection("k", None))
    assert asyncio.run(engines.translate_tags(["candy", "long hair"], ctx)) == {
        "candy": "キャンディ"
    }
    style, kwargs = client.calls[0]
    assert style == "responses"
    assert kwargs["model"] == "llm-x"
    assert kwargs["input"][0]["content"][0]["text"] == "candy\nlong hair"


def test_openai_title_instructions_forbid_symbols(tmp_path: Path) -> None:
    client = _FakeClient("**夜の港**。")
    engines = OpenAIEngines(WdTagger(tmp_path))
    engines._client = client  # type: ignore[assignment]
    engines._client_key = ("k", None)
    ctx = EngineContext(AnnotationConfig(), Connection("k", None))
    assert asyncio.run(engines.title_from_prompt("harbor", ctx)) == "夜の港"
    assert "引用符" in client.calls[0][1]["instructions"]

"""Run のプロンプトの埋め込み(ADR-0045)。

- 埋め込む文章の組み立て(最終プロンプト、タイトル、上限)。
- 待ち行列と worker(自動実行、一括実行、タイトルの変更での埋め込み直し)。FAKE プロバイダーの
  アプリ(`client`)では、埋め込みのエンジンもダミー(1文字を1トークンとして数える)。
- `GET /api/search/semantic?target=prompt`。
- 近傍検索(`prompt_top_k`)は numpy と pgvector の両方で同じテストを通す(pgvector は
  `GAKEI_TEST_DATABASE_URL` が拡張 `vector` を使える PostgreSQL のときだけ)。
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.auth.identity import LOCAL_ADMIN, CurrentUser
from app.domain import embedding_index
from app.domain import prompt_embeddings as domain
from app.domain.models import (
    AppUser,
    Asset,
    AssetKind,
    Run,
    RunOperation,
    RunPromptEmbedding,
    RunStatus,
)
from app.domain.vector_index import AssetFilter, NumpyIndex, PgvectorIndex, RunFilter
from app.embedding.base import l2_normalize, vector_to_blob
from app.embedding.catalog import (
    EG2_DOCUMENT_PREFIX,
    EG2_MAX_TEXT_TOKENS,
    EG2_ONNX_REVISION,
    XENOVA_CLIP_REVISION,
)
from app.embedding.fake import FakeEmbeddingEngine
from tests.conftest import wait_for_run_terminal

EG2_KEY = f"fake:onnx:embeddinggemma-2-q8@{EG2_ONNX_REVISION}"
CLIP_KEY = f"fake:onnx:clip-vit-b32-u8@{XENOVA_CLIP_REVISION}"
# ダミーのエンジンで、プレフィックスを付けて上限に収まるプロンプトの最大の文字数。
FAKE_BODY_LIMIT = EG2_MAX_TEXT_TOKENS - 2 - len(EG2_DOCUMENT_PREFIX)


# -- 埋め込む文章 -------------------------------------------------------------------


def _run(prompt: str = "a cat", text_outputs: Any = None) -> Run:
    return Run(
        provider="fake",
        model="m",
        operation=RunOperation.GENERATE,
        prompt=prompt,
        params={},
        status=RunStatus.SUCCEEDED,
        text_outputs=text_outputs,
    )


def test_select_prompt_prefers_final_prompt() -> None:
    assert domain.select_prompt(_run("orig")) == "orig"
    # ネガティブは使わない。空の最終プロンプトは無視する。
    outputs = [
        {"role": "final_negative_prompt", "output_index": 0, "text": "neg"},
        {"role": "final_prompt", "output_index": 1, "text": "second"},
        {"role": "final_prompt", "output_index": 0, "text": "first"},
        {"role": "final_prompt", "output_index": 2, "text": "  "},
    ]
    assert domain.select_prompt(_run("orig", outputs)) == "first"
    # Run 全体の最終プロンプト(output_index が null)を先にする。
    whole = [*outputs, {"role": "final_prompt", "output_index": None, "text": "whole"}]
    assert domain.select_prompt(_run("orig", whole)) == "whole"
    assert domain.select_prompt(_run("orig", [{"role": "final_prompt", "text": ""}])) == "orig"
    assert domain.select_prompt(_run("orig", "broken")) == "orig"


def test_build_document_adds_title_only_when_over_limit() -> None:
    def fits(text: str) -> bool:
        return len(text) <= 10

    short = domain.build_document("short", "Title", fits)
    assert (short.text, short.truncated) == ("short", False)
    long = domain.build_document("x" * 20, "Title", fits)
    assert (long.text, long.truncated) == ("Title\n" + "x" * 20, True)
    assert long.sha256 == domain.text_sha256("Title\n" + "x" * 20)
    untitled = domain.build_document("x" * 20, None, fits)
    assert (untitled.text, untitled.truncated) == ("x" * 20, True)
    blank = domain.build_document("x" * 20, "  ", fits)
    assert blank.text == "x" * 20


def test_fake_engine_documents_follow_the_limit() -> None:
    engine = FakeEmbeddingEngine(
        "k", max_text_tokens=EG2_MAX_TEXT_TOKENS, document_prefix=EG2_DOCUMENT_PREFIX
    )
    assert engine.document_fits("a" * FAKE_BODY_LIMIT)
    assert not engine.document_fits("a" * (FAKE_BODY_LIMIT + 1))
    # 上限を超えた分は切る(切った後の文章と同じベクトル)。
    cut = engine.embed_documents(["a" * (FAKE_BODY_LIMIT + 50)])
    same = engine.embed_documents(["a" * FAKE_BODY_LIMIT])
    assert np.allclose(cut, same)
    # 検索の文章と同じ文字列なら同じベクトル(ダミーのエンジンはプレフィックスを付けない)。
    assert np.allclose(engine.embed_documents(["a cat"]), engine.embed_texts(["a cat"]))
    assert engine.text_calls == [1]


# -- API と worker -------------------------------------------------------------------


def _patch(client: TestClient, **values: Any) -> dict:
    response = client.patch("/api/settings/embeddings", json=values)
    assert response.status_code == 200, response.text
    return response.json()


def _generate(client: TestClient, prompt: str, n: int = 1) -> dict:
    response = client.post(
        "/api/runs",
        json={
            "operation": "generate",
            "model": "gpt-image-2.5-sunburst",
            "prompt": prompt,
            "params": {"n": n, "size": "1024x1024"},
        },
    )
    assert response.status_code == 202, response.text
    run = wait_for_run_terminal(client, response.json()["id"])
    assert run["status"] == "succeeded"
    return run


def _prompt_rows(client: TestClient, run_id: str | None = None) -> list[RunPromptEmbedding]:
    with client.app.state.session_factory() as session:
        query = select(RunPromptEmbedding)
        if run_id is not None:
            query = query.where(RunPromptEmbedding.run_id == uuid.UUID(run_id))
        return list(session.execute(query).scalars().all())


def _wait_prompt(
    client: TestClient,
    run_id: str,
    statuses: tuple[str, ...] = ("succeeded", "failed"),
    sha: str | None = None,
) -> RunPromptEmbedding:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        rows = [r for r in _prompt_rows(client, run_id) if r.model_key == EG2_KEY]
        if rows and rows[0].status in statuses and (sha is None or rows[0].text_sha256 == sha):
            return rows[0]
        time.sleep(0.05)
    raise TimeoutError(f"run {run_id} のプロンプトの埋め込みが終わりませんでした")


def test_clip_models_do_not_embed_prompts(client: TestClient) -> None:
    body = _patch(client, enabled=True)
    assert body["active_model_key"] == CLIP_KEY
    assert body["prompt_supported"] is False
    # 指示で探せるかは、管理者でなくても読める capabilities でも分かる(ADR-0045 6章)。
    caps = client.get("/api/capabilities").json()["embeddings"]
    assert caps["available"] is True
    assert caps["prompt_search"] is False
    run = _generate(client, "a boat")
    time.sleep(1.2)
    assert _prompt_rows(client) == []
    assert client.post("/api/settings/embeddings/backfill").json() == {
        "queued": 0,
        "prompt_queued": 0,
    }
    response = client.get("/api/search/semantic", params={"q": "boat", "target": "prompt"})
    assert response.status_code == 422
    # 画像の検索(既定)は今までどおり。
    image = client.get("/api/search/semantic", params={"q": "boat"}).json()
    assert image["target"] == "image"
    assert image["runs"] == []
    assert {a["id"] for a in image["assets"]} == {run["outputs"][0]["asset_id"]}


def test_auto_embeds_prompt_and_searches_runs(client: TestClient) -> None:
    assert client.get("/api/capabilities").json()["embeddings"]["prompt_search"] is False
    body = _patch(client, enabled=True, onnx_model="embeddinggemma-2-q8")
    assert body["prompt_supported"] is True
    assert client.get("/api/capabilities").json()["embeddings"]["prompt_search"] is True
    boat = _generate(client, "a red boat on the sea", n=2)
    cat = _generate(client, "a sleeping cat")
    row = _wait_prompt(client, boat["id"])
    assert row.status == "succeeded"
    assert row.truncated is False
    assert row.dim == 512
    assert row.text_sha256 == domain.text_sha256("a red boat on the sea")
    _wait_prompt(client, cat["id"])

    settings = client.get("/api/settings/embeddings").json()
    assert settings["prompt_stored_count"] == 2
    assert (
        settings["prompt_pending_count"],
        settings["prompt_queued_count"],
        settings["prompt_failed_count"],
    ) == (0, 0, 0)

    response = client.get(
        "/api/search/semantic", params={"q": "a red boat on the sea", "target": "prompt"}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["target"] == "prompt"
    assert result["model_key"] == EG2_KEY
    assert result["assets"] == []
    runs = result["runs"]
    # Run の単位(出力が2枚でも1件)。同じ文章なので類似度は 1。
    assert [r["id"] for r in runs][:1] == [boat["id"]]
    assert len(runs) == 2
    first = runs[0]
    assert first["score"] == pytest.approx(1.0)
    assert first["operation"] == "generate"
    assert first["prompt"] == "a red boat on the sea"
    assert first["prompt_truncated"] is False
    assert first["output"]["id"] == boat["outputs"][0]["asset_id"]
    # 履歴のカード用の要約(キーワード検索の Run と同じ形)。
    summary = first["run"]
    assert summary["id"] == boat["id"]
    assert summary["status"] == "succeeded"
    assert summary["prompt"] == "a red boat on the sea"
    assert [o["asset_id"] for o in summary["outputs"]] == [o["asset_id"] for o in boat["outputs"]]
    assert summary["input_count"] == 0

    # 画像の絞り込みは Run には掛けられない。
    bad = client.get(
        "/api/search/semantic", params={"q": "boat", "target": "prompt", "kind": "generated"}
    )
    assert bad.status_code == 422

    # 削除した Run は出さない。
    assert client.delete(f"/api/runs/{boat['id']}").status_code in (200, 204)
    again = client.get(
        "/api/search/semantic", params={"q": "a red boat on the sea", "target": "prompt"}
    ).json()
    assert [r["id"] for r in again["runs"]] == [cat["id"]]


def test_long_prompt_is_truncated_and_title_reembeds(client: TestClient) -> None:
    _patch(client, enabled=True, onnx_model="embeddinggemma-2-q8")
    long_prompt = "a lighthouse " * ((FAKE_BODY_LIMIT // 13) + 10)
    assert len(long_prompt) > FAKE_BODY_LIMIT
    run = _generate(client, long_prompt)
    row = _wait_prompt(client, run["id"])
    assert row.truncated is True
    # タイトルがまだ無いので、プロンプトだけ。
    assert row.text_sha256 == domain.text_sha256(long_prompt)

    asset_id = run["outputs"][0]["asset_id"]
    response = client.patch(f"/api/assets/{asset_id}/title", json={"title": "Lighthouse"})
    assert response.status_code == 200, response.text
    expected = domain.text_sha256("Lighthouse\n" + long_prompt)
    row = _wait_prompt(client, run["id"], ("succeeded",), sha=expected)
    assert row.truncated is True

    # 同じタイトルなら計算し直さない。
    finished = row.finished_at
    client.patch(f"/api/assets/{asset_id}/title", json={"title": "Lighthouse"})
    time.sleep(0.3)
    assert _wait_prompt(client, run["id"]).finished_at == finished


def test_short_prompt_title_change_does_not_reembed(client: TestClient) -> None:
    _patch(client, enabled=True, onnx_model="embeddinggemma-2-q8")
    run = _generate(client, "a short prompt")
    row = _wait_prompt(client, run["id"])
    finished = row.finished_at
    asset_id = run["outputs"][0]["asset_id"]
    client.patch(f"/api/assets/{asset_id}/title", json={"title": "Something"})
    time.sleep(0.3)
    row = _wait_prompt(client, run["id"])
    assert row.finished_at == finished
    assert row.text_sha256 == domain.text_sha256("a short prompt")


def test_backfill_queues_prompts_with_images(client: TestClient) -> None:
    _patch(client, enabled=True, onnx_model="embeddinggemma-2-q8", auto_on_ingest=False)
    run = _generate(client, "a mountain")
    time.sleep(0.3)
    assert _prompt_rows(client) == []
    settings = client.get("/api/settings/embeddings").json()
    assert settings["prompt_pending_count"] == 1
    body = client.post("/api/settings/embeddings/backfill").json()
    assert body == {"queued": 1, "prompt_queued": 1}
    assert _wait_prompt(client, run["id"]).status == "succeeded"
    assert client.post("/api/settings/embeddings/backfill").json() == {
        "queued": 0,
        "prompt_queued": 0,
    }
    # 失敗した行は一括実行の対象に戻る。
    with client.app.state.session_factory() as session:
        row = session.get(RunPromptEmbedding, (uuid.UUID(run["id"]), EG2_KEY))
        assert row is not None
        row.status = "failed"
        session.commit()
    assert client.get("/api/settings/embeddings").json()["prompt_failed_count"] == 1
    assert client.post("/api/settings/embeddings/backfill").json()["prompt_queued"] == 1
    assert _wait_prompt(client, run["id"], ("succeeded",)).error is None


def test_delete_vectors_removes_prompt_rows(client: TestClient) -> None:
    _patch(client, enabled=True, onnx_model="embeddinggemma-2-q8")
    run = _generate(client, "a forest")
    _wait_prompt(client, run["id"])
    response = client.delete(f"/api/settings/embeddings/vectors/{EG2_KEY}")
    assert response.status_code == 200, response.text
    assert _prompt_rows(client) == []


def test_failed_runs_are_not_targets(client: TestClient) -> None:
    _patch(client, enabled=True, onnx_model="embeddinggemma-2-q8")
    with client.app.state.session_factory() as session:
        failed = _run("a failed prompt")
        failed.status = RunStatus.FAILED
        session.add(failed)
        session.commit()
        assert domain.is_target(session, failed) is False
        assert domain.pending_count(session, EG2_KEY) == 0


# -- 近傍検索(numpy と pgvector) ----------------------------------------------------

MODEL = "test:model@1"
DIM = 16


class _Versions:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def __call__(self, key: str) -> int:
        return self.values.get(key, 0)

    def bump(self) -> None:
        key = domain.version_key(MODEL)
        self.values[key] = self.values.get(key, 0) + 1


def _pgvector_available(factory: sessionmaker) -> bool:
    engine = factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        return False
    with engine.connect() as conn:
        return embedding_index.has_embedding_column(conn, embedding_index.PROMPT_TABLE)


@pytest.fixture(params=["numpy", "pgvector"])
def backend(request: pytest.FixtureRequest, db_session_factory: sessionmaker) -> str:
    if request.param == "pgvector" and not _pgvector_available(db_session_factory):
        pytest.skip("pgvector の PostgreSQL が無い")
    return request.param


class _World:
    def __init__(self, factory: sessionmaker, backend: str) -> None:
        self.factory = factory
        self.backend = backend
        self.versions = _Versions()
        self.index = PgvectorIndex() if backend == "pgvector" else NumpyIndex(self.versions)
        self.vectors: dict[uuid.UUID, np.ndarray] = {}
        self._t0 = datetime(2026, 10, 1, tzinfo=UTC)
        self._n = 0

    def user(self, email: str) -> CurrentUser:
        with self.factory() as db:
            row = AppUser(issuer="https://idp.test", subject=email, email=email, role="user")
            db.add(row)
            db.commit()
            return CurrentUser(id=row.id, name=None, email=email, role="user")  # type: ignore[arg-type]

    def run(
        self,
        vector: np.ndarray,
        *,
        owner: uuid.UUID | None = None,
        deleted: bool = False,
        status: RunStatus = RunStatus.SUCCEEDED,
    ) -> uuid.UUID:
        self._n += 1
        with self.factory() as db:
            run = Run(
                provider="fake",
                model="m",
                operation=RunOperation.EDIT,
                prompt=f"prompt {self._n}",
                params={},
                status=status,
                created_by_user_id=owner,
                queued_at=self._t0 + timedelta(minutes=self._n),
                deleted_at=self._t0 if deleted else None,
            )
            db.add(run)
            db.flush()
            db.add(
                Asset(
                    kind=AssetKind.GENERATED,
                    sha256=f"{self._n:064x}",
                    blob_key=f"assets/test/{self._n}",
                    mime="image/png",
                    width=10,
                    height=10,
                    bytes=1,
                    created_at=self._t0 + timedelta(minutes=self._n),
                    produced_by_run_id=run.id,
                    output_index=0,
                    created_by_user_id=owner,
                )
            )
            values = l2_normalize(vector)[0]
            db.add(
                RunPromptEmbedding(
                    run_id=run.id,
                    model_key=MODEL,
                    status="succeeded",
                    dim=DIM,
                    vector=vector_to_blob(values),
                    truncated=False,
                    updated_at=self._t0,
                )
            )
            db.flush()
            if self.backend == "pgvector":
                embedding_index.write_embedding_column(
                    db.connection(), run.id, MODEL, values, embedding_index.PROMPT_TABLE
                )
            db.commit()
            self.vectors[run.id] = values
            return run.id

    def finish(self) -> None:
        self.versions.bump()
        if self.backend == "pgvector":
            assert embedding_index.ensure_hnsw_index(
                self.factory.kw["bind"], MODEL, DIM, embedding_index.PROMPT_TABLE
            )

    def expected(self, query: np.ndarray, ids: list[uuid.UUID], k: int) -> list[uuid.UUID]:
        q = l2_normalize(query)[0]
        return sorted(ids, key=lambda i: (-float(self.vectors[i] @ q), str(i)))[:k]


def test_prompt_top_k_parity_and_visibility(db_session_factory: sessionmaker, backend: str) -> None:
    rng = np.random.default_rng(3)
    world = _World(db_session_factory, backend)
    alice = world.user("alice@example.com")
    bob = world.user("bob@example.com")
    alice_ids = [world.run(rng.standard_normal(DIM), owner=alice.id) for _ in range(10)]
    bob_ids = [world.run(rng.standard_normal(DIM), owner=bob.id) for _ in range(10)]
    world.run(rng.standard_normal(DIM), owner=alice.id, deleted=True)
    world.run(rng.standard_normal(DIM), owner=alice.id, status=RunStatus.FAILED)
    world.finish()

    query = rng.standard_normal(DIM).astype(np.float32)
    with db_session_factory() as db:
        hits = world.index.prompt_top_k(db, MODEL, query, RunFilter(viewer=LOCAL_ADMIN), 5)
        assert [h.run_id for h in hits] == world.expected(query, alice_ids + bob_ids, 5)
        assert hits[0].score >= hits[-1].score
        mine = world.index.prompt_top_k(db, MODEL, query, RunFilter(viewer=alice), 50)
        assert {h.run_id for h in mine} == set(alice_ids)
        # 次元が違う問い合わせ、ベクトルの無いモデルは空。
        assert world.index.prompt_top_k(db, MODEL, query[:4], RunFilter(LOCAL_ADMIN), 5) == []
        assert world.index.prompt_top_k(db, "other", query, RunFilter(LOCAL_ADMIN), 5) == []


def test_numpy_prompt_matrix_is_separate_from_images(db_session_factory: sessionmaker) -> None:
    rng = np.random.default_rng(5)
    world = _World(db_session_factory, "numpy")
    first = world.run(rng.standard_normal(DIM))
    world.finish()
    index = world.index
    assert isinstance(index, NumpyIndex)
    with db_session_factory() as db:
        query = world.vectors[first]
        assert [h.run_id for h in index.prompt_top_k(db, MODEL, query, RunFilter(LOCAL_ADMIN), 3)]
        # 画像の行列は別に持つ(画像を探しても、プロンプトの行列を捨てない)。
        assert index.top_k(db, MODEL, query, AssetFilter(viewer=LOCAL_ADMIN), 3) == []
        loads = index.loads
        index.prompt_top_k(db, MODEL, query, RunFilter(LOCAL_ADMIN), 3)
        assert index.loads == loads
        second = world.run(rng.standard_normal(DIM))
        # 版が変わるまでは読み直さない。
        hits = index.prompt_top_k(db, MODEL, query, RunFilter(LOCAL_ADMIN), 3)
        assert second not in {h.run_id for h in hits}
        world.versions.bump()
        hits = index.prompt_top_k(db, MODEL, query, RunFilter(LOCAL_ADMIN), 3)
        assert second in {h.run_id for h in hits}

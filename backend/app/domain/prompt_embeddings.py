"""Run のプロンプトの埋め込みの待ち行列と、埋め込む文章の組み立て(ADR-0045)。

埋め込みは証跡ではない。来歴の列(`run`、`run_input`、`asset`)には書き込まない。行は
(Run、`model_key`)ごとで、`status` が待ち行列を兼ねる(作りは画像の `embeddings.py` と同じ)。

- 対象は、出力のある成功した Run(Generate と Edit)。失敗・中止・削除済みの Run は入れない。
  プロンプトが空の Run も入れない。
- 使うモデルがプロンプトの埋め込みに対応するとき(カタログの `prompt_embeddings`。今は
  EmbeddingGemma 2 だけ)だけ動く。CLIP 系やリモートを選んでいる間は何もしない。
- 埋め込む文章(2章): `text_outputs` の `final_prompt` があればそれ、無ければ `run.prompt`
  (ネガティブは使わない)。文書のプレフィックスを付けて上限を超えるときだけ、最初の出力
  (`output_index = 0`)の Asset のタイトルを先頭に付ける(`{タイトル}\\n{プロンプト}`)。
  プレフィックスと上限での切り詰めはエンジンが行う。
- `text_sha256` は、組み立てた文章(タイトル込み。プレフィックスは含めない)の sha256。
  `truncated` は、プロンプトが上限を超えたとき True(このときだけタイトルを付ける)。
- 呼び出し側がトランザクション(commit)を制御できるよう、ここでは `flush` までに留める。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.orm import Session

from app.domain import embedding_settings
from app.domain.embeddings import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
)
from app.domain.models import Asset, AssetAnnotation, Run, RunPromptEmbedding, RunStatus

if TYPE_CHECKING:
    from app.config import Settings

__all__ = [
    "STATUS_FAILED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
]

# numpy の検索で、プロンプトの行列を読み直す目安にする版のキー(`Embedder.version`)。画像の
# `model_key`(`onnx:`、`remote:`、`fake:` で始まる)とは重ならない。
VERSION_KEY_PREFIX = "prompt:"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def version_key(model_key: str) -> str:
    return VERSION_KEY_PREFIX + model_key


# -- 使うモデル ------------------------------------------------------------------


def active_model_key(config: embedding_settings.EmbeddingConfig, settings: Settings) -> str | None:
    """プロンプトの埋め込みに使う `model_key`。埋め込みが使えない、または使うモデルが対応して
    いなければ None。"""
    if not embedding_settings.prompt_supported(config):
        return None
    if not embedding_settings.usable(config, settings):
        return None
    return embedding_settings.active_model_key(config, settings)


# -- 埋め込む文章 -----------------------------------------------------------------


def select_prompt(run: Run) -> str:
    """埋め込むプロンプト。`final_prompt` があればそれ(Run 全体のもの、無ければ
    `output_index` の小さいもの)、無ければ `run.prompt`。"""
    raw = run.text_outputs
    if isinstance(raw, list):
        candidates = [
            item
            for item in raw
            if isinstance(item, dict)
            and item.get("role") == "final_prompt"
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ]

        def order(item: dict[str, Any]) -> tuple[int, int]:
            index = item.get("output_index")
            if index is None:
                return (0, 0)
            return (1, index if isinstance(index, int) else 1 << 30)

        if candidates:
            return str(sorted(candidates, key=order)[0]["text"])
    return run.prompt or ""


@dataclass(frozen=True)
class DocumentText:
    text: str
    # プロンプトが上限を超えた(タイトルがあれば先頭に付け、エンジンが切る)。
    truncated: bool

    @property
    def sha256(self) -> str:
        return text_sha256(self.text)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def with_title(prompt: str, title: str | None) -> str:
    """上限を超えるプロンプトに、タイトルを先頭に付けた文章(タイトルが無ければそのまま)。"""
    title = (title or "").strip()
    return f"{title}\n{prompt}" if title else prompt


def build_document(prompt: str, title: str | None, fits: Callable[[str], bool]) -> DocumentText:
    """埋め込む文章を組み立てる(ADR-0045 2章)。`fits` は、文書のプレフィックスを付けた文章が
    上限に収まるか(エンジンの `document_fits`)。収まればそのまま、超えるときだけタイトルを
    先頭に付ける。"""
    if fits(prompt):
        return DocumentText(prompt, truncated=False)
    return DocumentText(with_title(prompt, title), truncated=True)


def first_output_title(db: Session, run_id: uuid.UUID) -> str | None:
    """Run の最初の出力(`output_index = 0`)の Asset のタイトル(自動・人の編集どちらも)。"""
    return db.execute(
        select(AssetAnnotation.title)
        .join(Asset, Asset.id == AssetAnnotation.asset_id)
        .where(Asset.produced_by_run_id == run_id, Asset.output_index == 0)
        .limit(1)
    ).scalar_one_or_none()


# -- 対象 -------------------------------------------------------------------------


def _has_output() -> Any:
    return exists().where(Asset.produced_by_run_id == Run.id)


def _target_conditions() -> list[Any]:
    return [
        Run.status == RunStatus.SUCCEEDED,
        Run.deleted_at.is_(None),
        Run.prompt != "",
        _has_output(),
    ]


def is_target(db: Session, run: Run) -> bool:
    """計算の対象か(成功、削除されていない、出力がある、プロンプトが空でない)。"""
    if run.status != RunStatus.SUCCEEDED or run.deleted_at is not None:
        return False
    if not select_prompt(run).strip():
        return False
    return (
        db.execute(select(Asset.id).where(Asset.produced_by_run_id == run.id).limit(1)).first()
        is not None
    )


# -- 待ち行列 -----------------------------------------------------------------------


def _queue(row: RunPromptEmbedding, now: datetime) -> None:
    row.status = STATUS_QUEUED
    row.error = None
    row.requested_at = now
    row.finished_at = None
    row.updated_at = now


def request(db: Session, run_id: uuid.UUID, model_key: str) -> bool:
    """1件の(再)計算を待ち行列に入れる。既に queued / running なら何もしない(False)。
    計算済みのベクトルは、計算し直すまで残す。"""
    row = db.get(RunPromptEmbedding, (run_id, model_key))
    if row is None:
        row = RunPromptEmbedding(run_id=run_id, model_key=model_key, status=STATUS_QUEUED)
        db.add(row)
    elif row.status in (STATUS_QUEUED, STATUS_RUNNING):
        return False
    _queue(row, _utcnow())
    db.flush()
    return True


def enqueue_on_success(db: Session, run: Run, settings: Settings) -> bool:
    """Run が成功したときの自動実行(ADR-0045 4章)。埋め込みの自動実行がオフ、使えるモデルが
    無い、モデルが対応していない、対象でなければ何もしない。入れたら True(呼び出し側は commit
    後に worker を起こす)。"""
    config = embedding_settings.load(db)
    if not config.auto_on_ingest:
        return False
    model_key = active_model_key(config, settings)
    if model_key is None or not is_target(db, run):
        return False
    return request(db, run.id, model_key)


def _outer_join_for(model_key: str) -> Any:
    return and_(RunPromptEmbedding.run_id == Run.id, RunPromptEmbedding.model_key == model_key)


def _pending_condition() -> Any:
    """ベクトルが無い(行が無い、または失敗した)対象の Run。待ち行列にあるものは含めない。"""
    return and_(
        *_target_conditions(),
        or_(RunPromptEmbedding.run_id.is_(None), RunPromptEmbedding.status == STATUS_FAILED),
    )


def pending_count(db: Session, model_key: str) -> int:
    """一括実行の対象の件数(失敗したものを含む)。"""
    return db.execute(
        select(func.count())
        .select_from(Run)
        .outerjoin(RunPromptEmbedding, _outer_join_for(model_key))
        .where(_pending_condition())
    ).scalar_one()


def status_count(db: Session, model_key: str, statuses: tuple[str, ...]) -> int:
    return db.execute(
        select(func.count())
        .select_from(RunPromptEmbedding)
        .where(RunPromptEmbedding.model_key == model_key, RunPromptEmbedding.status.in_(statuses))
    ).scalar_one()


def stored_count(db: Session, model_key: str) -> int:
    """計算済みのベクトルの件数。"""
    return db.execute(
        select(func.count())
        .select_from(RunPromptEmbedding)
        .where(
            RunPromptEmbedding.model_key == model_key,
            RunPromptEmbedding.status == STATUS_SUCCEEDED,
            RunPromptEmbedding.vector.is_not(None),
        )
    ).scalar_one()


def known_dim(db: Session, model_key: str) -> int | None:
    return db.execute(
        select(RunPromptEmbedding.dim)
        .where(
            RunPromptEmbedding.model_key == model_key,
            RunPromptEmbedding.status == STATUS_SUCCEEDED,
            RunPromptEmbedding.dim.is_not(None),
        )
        .limit(1)
    ).scalar_one_or_none()


def backfill(db: Session, model_key: str) -> int:
    """ベクトルが無い対象の Run(失敗したものを含む)をまとめて待ち行列に入れ、件数を返す
    (画像の一括実行と同じ規則。ADR-0045 4章)。"""
    rows = db.execute(
        select(Run.id, RunPromptEmbedding)
        .outerjoin(RunPromptEmbedding, _outer_join_for(model_key))
        .where(_pending_condition())
        .order_by(Run.queued_at.asc(), Run.id.asc())
    ).all()
    now = _utcnow()
    for run_id, row in rows:
        if row is None:
            row = RunPromptEmbedding(run_id=run_id, model_key=model_key, status=STATUS_QUEUED)
            db.add(row)
        _queue(row, now)
    db.flush()
    return len(rows)


def delete_vectors(db: Session, model_key: str) -> int:
    """そのモデルの行(ベクトルと状態)をすべて消し、件数を返す。"""
    result = db.execute(delete(RunPromptEmbedding).where(RunPromptEmbedding.model_key == model_key))
    db.flush()
    return result.rowcount or 0


# -- タイトルの変更 -----------------------------------------------------------------


def on_title_changed(db: Session, asset: Asset, settings: Settings) -> bool:
    """Asset のタイトルが変わったあとに呼ぶ(人の編集、自動のタイトルの完了。ADR-0045 2章)。

    その Asset が Run の最初の出力で、Run のプロンプトが上限を超えていた(`truncated`)ときだけ、
    埋め込む文章の sha256 が変わるなら待ち行列に戻す。計算中なら、終わった結果を捨てて計算し
    直すよう待ち行列に戻す(worker は `running` のままの行にだけ結果を書く)。上限を超えるかは
    前回の計算で分かっているので、ここではトークナイザーを使わない。戻したら True。
    """
    if asset.produced_by_run_id is None or asset.output_index != 0:
        return False
    model_key = active_model_key(embedding_settings.load(db), settings)
    if model_key is None:
        return False
    row = db.get(RunPromptEmbedding, (asset.produced_by_run_id, model_key))
    if row is None or not row.truncated or row.status in (STATUS_QUEUED, STATUS_FAILED):
        return False
    run = db.get(Run, asset.produced_by_run_id)
    if run is None or not is_target(db, run):
        return False
    if row.status != STATUS_RUNNING:
        db.flush()
        text = with_title(select_prompt(run), first_output_title(db, run.id))
        if text_sha256(text) == row.text_sha256:
            return False
    _queue(row, _utcnow())
    db.flush()
    return True

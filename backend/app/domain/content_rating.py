"""画像のレーティング(性表現の強さの段階。ADR-0049)。判定の規則、保存、待ち行列への投入、
一覧向けの読み出しをまとめる。

段階は5つ(`g`、`r`、`x`、`xx`、`xxx`)。WD Tagger v3 の rating の4つの確率から基準の段階を
決め(累積の確率が 0.5 以上になる最も強い段階。中央値)、一般タグの確率で引き上げる(1章)。
規則(表と累積のしきい値)はここに置き、版 `RULES_VERSION` を持たせる。表を変えたら版を上げる。

保存(2章): `asset_content_rating` は証跡ではないので更新してよい。人が段階を選ぶと
`source = user`、`level` を人の段階にする。「自動に戻す」で `source = auto`、`level = auto_level`。
再判定は `auto_level` 以下の列だけを書き換え、`source = user` なら `level` を変えない。

判定そのもの(推論)は `app/worker/annotator.py` が行う。待ち行列は `status = 'queued'` の行。
呼び出し側がトランザクション(commit)を制御できるよう、ここでは `flush` までに留める。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.domain import annotation_settings
from app.domain.models import Asset, AssetContentRating, AssetKind
from app.domain.schemas import ContentRatingDetail, ContentRatingSignal

if TYPE_CHECKING:
    from app.config import Settings

# -- 段階と規則(1章) ---------------------------------------------------------------

LEVEL_G = "g"
LEVEL_R = "r"
LEVEL_X = "x"
LEVEL_XX = "xx"
LEVEL_XXX = "xxx"
# 弱い順。
LEVELS: tuple[str, ...] = (LEVEL_G, LEVEL_R, LEVEL_X, LEVEL_XX, LEVEL_XXX)
_LEVEL_INDEX = {level: i for i, level in enumerate(LEVELS)}

# 判定の規則の版。表やしきい値を変えたら上げる(判定済みの画像は自動では判定し直さない。
# 管理者設定の一括判定が古い版の自動判定も対象にする)。
RULES_VERSION = 1

# WD の rating のラベル(弱い順)と、対応する基準の段階。
RATING_LABELS: tuple[str, ...] = ("general", "sensitive", "questionable", "explicit")
_BASE_LEVELS: tuple[tuple[str, str], ...] = (
    ("general", LEVEL_G),
    ("sensitive", LEVEL_R),
    ("questionable", LEVEL_X),
    ("explicit", LEVEL_XX),
)
# 累積の確率がこれ以上になる最も強い段階を基準にする(中央値)。
MEDIAN_THRESHOLD = 0.5
# 判定の根拠(`signals`)に残す、引き上げのタグの確率の下限。
SIGNAL_MIN_SCORE = 0.2


@dataclass(frozen=True)
class BoostRule:
    """一般タグによる引き上げの1行。`tags` のどれかの確率が `threshold` 以上なら、段階を
    少なくとも `level` まで上げる。`requires` があれば、それまでの段階がそれ以上のときだけ。"""

    level: str
    tags: tuple[str, ...]
    threshold: float
    requires: str | None = None


# ADR-0049 1章の表。タグは WD の表記(`_` 区切り)。
BOOST_RULES: tuple[BoostRule, ...] = (
    BoostRule(
        LEVEL_R,
        ("swimsuit", "bikini", "underwear", "panties", "bra", "lingerie", "cleavage"),
        0.6,
    ),
    BoostRule(
        LEVEL_X,
        ("nipples", "topless", "bottomless", "nude", "completely_nude"),
        0.5,
    ),
    BoostRule(
        LEVEL_XX,
        ("pussy", "penis", "testicles", "anus", "clitoris", "erection"),
        0.5,
    ),
    BoostRule(
        LEVEL_XXX,
        (
            "sex",
            "vaginal",
            "anal",
            "oral",
            "fellatio",
            "cunnilingus",
            "paizuri",
            "handjob",
            "footjob",
            "masturbation",
            "group_sex",
            "rape",
            "cum",
            "ejaculation",
            "cum_in_pussy",
            "sex_toy",
            "dildo",
            "vibrator",
        ),
        0.5,
        requires=LEVEL_XX,
    ),
)

# 推論で確率を取り出すタグ(タグのしきい値で捨てる前の値を使う)。
BOOST_TAGS: frozenset[str] = frozenset(tag for rule in BOOST_RULES for tag in rule.tags)

SOURCE_AUTO = "auto"
SOURCE_USER = "user"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

_ERROR_MAX = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


def level_index(level: str) -> int:
    return _LEVEL_INDEX[level]


def _max_level(a: str, b: str) -> str:
    return a if _LEVEL_INDEX[a] >= _LEVEL_INDEX[b] else b


def base_level(scores: Mapping[str, float]) -> str:
    """rating の4つの確率から基準の段階を決める。強い側から確率を足し、累積が 0.5 以上に
    なった最も強い段階を返す(どこでも届かなければ `g`)。"""
    cumulative = 0.0
    for label, level in reversed(_BASE_LEVELS):
        cumulative += float(scores.get(label, 0.0) or 0.0)
        # 浮動小数の足し算の誤差で 0.5 ちょうどを取りこぼさないよう、わずかに緩める。
        if cumulative >= MEDIAN_THRESHOLD - 1e-9:
            return level
    return LEVEL_G


def judge(
    scores: Mapping[str, float], tag_probs: Mapping[str, float]
) -> tuple[str, list[dict[str, Any]]]:
    """段階と判定の根拠(`signals`)を返す。

    `scores` は rating の4つの確率(`general` など)、`tag_probs` は引き上げのタグの確率
    (タグのしきい値で捨てる前の値。WD の表記)。根拠は引き上げの表に載っているタグのうち
    確率が 0.2 以上のもので、そのタグが段階を上げたなら `raised = True`。
    """
    base = base_level(scores)
    level = base
    # `requires` の無い行(r、x、xx)は基準の段階からの引き上げ。
    for rule in BOOST_RULES:
        if rule.requires is not None:
            continue
        if any(float(tag_probs.get(tag, 0.0)) >= rule.threshold for tag in rule.tags):
            level = _max_level(level, rule.level)
    before_conditional = level
    # `xxx` は、それまでの段階が `xx` 以上のときだけ(性行為のタグだけでは上げない)。
    for rule in BOOST_RULES:
        if rule.requires is None:
            continue
        if _LEVEL_INDEX[before_conditional] < _LEVEL_INDEX[rule.requires]:
            continue
        if any(float(tag_probs.get(tag, 0.0)) >= rule.threshold for tag in rule.tags):
            level = _max_level(level, rule.level)

    signals: list[dict[str, Any]] = []
    for rule in BOOST_RULES:
        reference = base if rule.requires is None else before_conditional
        condition_met = rule.requires is None or (
            _LEVEL_INDEX[before_conditional] >= _LEVEL_INDEX[rule.requires]
        )
        for tag in rule.tags:
            raw = tag_probs.get(tag)
            if raw is None:
                continue
            score = float(raw)
            if score < SIGNAL_MIN_SCORE:
                continue
            raised = (
                condition_met
                and score >= rule.threshold
                and _LEVEL_INDEX[rule.level] > _LEVEL_INDEX[reference]
            )
            signals.append(
                {"tag": tag, "score": round(score, 4), "level": rule.level, "raised": raised}
            )
    signals.sort(key=lambda s: (-s["score"], s["tag"]))
    return level, signals


# -- 使えるかどうか(3章) -------------------------------------------------------------


def model_ready(config: annotation_settings.AnnotationConfig, settings: Settings) -> bool:
    """判定に使う WD のモデル(`onnx_model`)をダウンロード済みか(`FAKE_PROVIDER=1` では
    ダミーを使うので常に True)。"""
    from app.annotation.wd_models import is_downloaded

    return settings.fake_provider or is_downloaded(settings.data_dir, config.onnx_model)


def usable(config: annotation_settings.AnnotationConfig, settings: Settings) -> bool:
    """レーティングの判定が有効で、モデルの準備もできているか。"""
    return config.content_rating_enabled and model_ready(config, settings)


# -- 行の取得と書き込み(2章) ---------------------------------------------------------


def _get_or_create(db: Session, asset_id: uuid.UUID) -> AssetContentRating:
    row = db.get(AssetContentRating, asset_id)
    if row is None:
        row = AssetContentRating(asset_id=asset_id, updated_at=_utcnow())
        db.add(row)
        db.flush()
    return row


def write_auto_result(
    db: Session,
    asset_id: uuid.UUID,
    *,
    model: str,
    scores: Mapping[str, float],
    tag_probs: Mapping[str, float],
) -> AssetContentRating:
    """自動判定の結果を書く。`auto_level` 以下の列だけを書き換え、人が選んだ段階
    (`source = user`)は変えない。待ち行列の状態は `succeeded` にする。"""
    level, signals = judge(scores, tag_probs)
    now = _utcnow()
    row = _get_or_create(db, asset_id)
    row.auto_level = level
    row.model = model
    row.rules_version = RULES_VERSION
    row.scores = {label: round(float(scores.get(label, 0.0)), 4) for label in RATING_LABELS}
    row.signals = signals
    row.status = STATUS_SUCCEEDED
    row.error = None
    row.judged_at = now
    row.updated_at = now
    if row.source != SOURCE_USER:
        row.source = SOURCE_AUTO
        row.level = level
    db.flush()
    return row


def mark_failed(db: Session, asset_id: uuid.UUID, message: str) -> None:
    """判定の失敗を記録する(自動では再試行しない)。段階は変えない。"""
    row = db.get(AssetContentRating, asset_id)
    if row is None:
        return
    now = _utcnow()
    row.status = STATUS_FAILED
    row.error = message[:_ERROR_MAX]
    row.updated_at = now
    db.flush()


def set_user_level(db: Session, asset: Asset, level: str | None) -> AssetContentRating:
    """人が段階を選ぶ。`None` は「自動に戻す」(`level = auto_level`。未判定なら NULL)。"""
    if level is not None and level not in _LEVEL_INDEX:
        raise ValueError(level)
    row = _get_or_create(db, asset.id)
    if level is None:
        row.source = SOURCE_AUTO if row.auto_level is not None else None
        row.level = row.auto_level
    else:
        row.source = SOURCE_USER
        row.level = level
    row.updated_at = _utcnow()
    db.flush()
    return row


# -- 待ち行列(3章) ---------------------------------------------------------------


def _queue(row: AssetContentRating, now: datetime) -> None:
    row.status = STATUS_QUEUED
    row.error = None
    row.requested_at = now
    row.updated_at = now


def request_judge(db: Session, asset: Asset) -> AssetContentRating:
    """1枚の(再)判定を待ち行列に入れる。既に queued / running なら何もしない。"""
    row = _get_or_create(db, asset.id)
    if row.status in (STATUS_QUEUED, STATUS_RUNNING):
        return row
    # 画像を残さなかった Asset(ADR-0051 4章)は判定しない。
    if asset.content_withheld_at is not None:
        return row
    _queue(row, _utcnow())
    db.flush()
    return row


def enqueue_on_ingest(db: Session, asset: Asset, settings: Settings) -> bool:
    """取り込み時の判定(ADR-0049 3章)。レーティングがオンでモデルの準備ができていれば、
    タイトル・タグの `auto_on_ingest` とは別に常に待ち行列に入れる。マスクと削除済みは
    入れない。入れたら True(呼び出し側は commit 後に worker を起こす)。"""
    if asset.kind == AssetKind.MASK or asset.deleted_at is not None:
        return False
    if asset.content_withheld_at is not None:
        return False
    config = annotation_settings.load(db)
    if not usable(config, settings):
        return False
    request_judge(db, asset)
    return True


def _pending_condition() -> Any:
    """一括判定の対象: 削除済みでない・マスク以外で、未判定(行が無い、または状態が NULL・
    失敗のまま段階が無い)か、古い版の規則で自動判定したもの。待ち行列にあるものは除く。"""
    unjudged = or_(
        AssetContentRating.asset_id.is_(None),
        and_(
            AssetContentRating.status.is_(None),
            AssetContentRating.auto_level.is_(None),
        ),
        and_(
            AssetContentRating.status == STATUS_FAILED,
            AssetContentRating.auto_level.is_(None),
        ),
    )
    outdated = and_(
        AssetContentRating.auto_level.is_not(None),
        or_(
            AssetContentRating.rules_version.is_(None),
            AssetContentRating.rules_version < RULES_VERSION,
        ),
        or_(
            AssetContentRating.status.is_(None),
            AssetContentRating.status.not_in((STATUS_QUEUED, STATUS_RUNNING)),
        ),
    )
    return and_(
        Asset.deleted_at.is_(None),
        Asset.content_withheld_at.is_(None),
        Asset.kind != AssetKind.MASK,
        or_(unjudged, outdated),
    )


def pending_count(db: Session) -> int:
    return db.execute(
        select(func.count())
        .select_from(Asset)
        .outerjoin(AssetContentRating, AssetContentRating.asset_id == Asset.id)
        .where(_pending_condition())
    ).scalar_one()


def queued_count(db: Session) -> int:
    return db.execute(
        select(func.count())
        .select_from(AssetContentRating)
        .where(AssetContentRating.status.in_((STATUS_QUEUED, STATUS_RUNNING)))
    ).scalar_one()


def backfill(db: Session) -> int:
    """未判定と、古い版の規則で自動判定した Asset をまとめて待ち行列に入れ、件数を返す。"""
    rows = db.execute(
        select(Asset.id, AssetContentRating)
        .outerjoin(AssetContentRating, AssetContentRating.asset_id == Asset.id)
        .where(_pending_condition())
        .order_by(Asset.created_at.asc(), Asset.id.asc())
    ).all()
    now = _utcnow()
    for asset_id, row in rows:
        if row is None:
            row = AssetContentRating(asset_id=asset_id, updated_at=now)
            db.add(row)
        _queue(row, now)
    db.flush()
    return len(rows)


def reset_running(session_factory: sessionmaker) -> int:
    """起動時に呼ぶ。`running` のままの行を `queued` に戻す(判定は来歴に加わらず、途中で
    止まっても害が無いため再開してよい)。"""
    with session_factory() as session:
        result = session.execute(
            update(AssetContentRating)
            .where(AssetContentRating.status == STATUS_RUNNING)
            .values(status=STATUS_QUEUED)
        )
        session.commit()
        return result.rowcount or 0


def pick_queued(session_factory: sessionmaker) -> uuid.UUID | None:
    """待ち行列の先頭(古い依頼から)を `running` にして返す。"""
    with session_factory() as session:
        candidate = session.execute(
            select(AssetContentRating.asset_id)
            .where(AssetContentRating.status == STATUS_QUEUED)
            # ADR-0027 2章: NULL の位置と同順位の並びを SQLite の挙動(NULL が先頭)にそろえる。
            .order_by(
                AssetContentRating.requested_at.asc().nulls_first(),
                AssetContentRating.asset_id.asc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None
        result = session.execute(
            update(AssetContentRating)
            .where(
                AssetContentRating.asset_id == candidate,
                AssetContentRating.status == STATUS_QUEUED,
            )
            .values(status=STATUS_RUNNING, updated_at=_utcnow())
        )
        session.commit()
        if (result.rowcount or 0) == 0:
            return None
        return candidate


# -- 画面・API 向けの読み出し(7章) -----------------------------------------------------

_IN_CHUNK = 500


def bulk_levels(db: Session, asset_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """asset_id ごとの段階(判定済み・人が選んだものだけ)。一覧でまとめて引くのに使う
    (N+1 を避ける)。"""
    ids = list(dict.fromkeys(asset_ids))
    found: dict[uuid.UUID, str] = {}
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start : start + _IN_CHUNK]
        rows = db.execute(
            select(AssetContentRating.asset_id, AssetContentRating.level).where(
                AssetContentRating.asset_id.in_(chunk),
                AssetContentRating.level.is_not(None),
            )
        ).all()
        for asset_id, level in rows:
            found[asset_id] = level
    return found


def level_for(db: Session, asset_id: uuid.UUID) -> str | None:
    row = db.get(AssetContentRating, asset_id)
    return row.level if row is not None else None


def _signals_view(raw: object) -> list[ContentRatingSignal]:
    if not isinstance(raw, list):
        return []
    result: list[ContentRatingSignal] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            result.append(ContentRatingSignal.model_validate(item))
        except ValueError:
            continue
    return result


def to_detail(row: AssetContentRating | None) -> ContentRatingDetail | None:
    if row is None:
        return None
    scores = row.scores if isinstance(row.scores, dict) else None
    return ContentRatingDetail(
        level=row.level,  # type: ignore[arg-type]
        source=row.source,  # type: ignore[arg-type]
        auto_level=row.auto_level,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        error=row.error,
        model=row.model,
        rules_version=row.rules_version,
        scores=(
            {label: float(scores[label]) for label in RATING_LABELS if label in scores}
            if scores is not None
            else None
        ),
        signals=_signals_view(row.signals),
        requested_at=row.requested_at,
        judged_at=row.judged_at,
    )


def detail_fields(db: Session, asset_id: uuid.UUID) -> dict[str, Any]:
    """`AssetDetail` に渡す `content_rating` と `content_rating_detail`。"""
    row = db.get(AssetContentRating, asset_id)
    return {
        "content_rating": row.level if row is not None else None,
        "content_rating_detail": to_detail(row),
    }

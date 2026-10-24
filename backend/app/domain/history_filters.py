"""履歴の絞り込みのうち、カードのラベルと対になるもの(形・透過・領域。ADR-0055 4章)。

カードのラベルの規則(`frontend/src/features/history/historyLabels.ts`)と同じ規則で SQL を作る。
しきい値や判断材料を変えるときは、両方を合わせて変える(両方にテストがある)。

- 形: 出力の実寸(`asset.width` / `asset.height`。NOT NULL)から決める。長辺 / 短辺が
  `SQUARE_MAX_RATIO`(1.1)以下なら正方形、それより縦長ならポートレート、横長ならワイド。
  浮動小数を避け、整数の掛け算(`長辺 * 10 <= 短辺 * 11`)で比べる。Run の絞り込みは
  「削除されていない出力のどれかがその形」。出力の無い Run(失敗など)はどの形にも当たらない
  (カードにも形のラベルを出さない)。
- 透過: `run.params.background == "transparent"` で、プロバイダーが OpenAI(と、それを模した fake)
  のもの。`params` は API に送った値そのもの(ADR-0003)なので、透過を頼んだことが確かに分かる。
  `auto` で結果が透過になったものや、ほかのプロバイダー(ComfyUI・SD WebUI は背景の指定を持たない)は
  判定できないので対象にしない。
- 領域: `run.regions.regions` に四角が1つ以上ある Run(ADR-0053 4章。作成時に四角がある
  ときだけ書く)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import ColumnElement, and_, select

from app.domain.models import Asset, Run

HistoryShape = Literal["square", "portrait", "wide"]

# 長辺 / 短辺がこの値以下なら正方形(フロントの `SQUARE_MAX_RATIO` と同じ)。分数で持ち、SQL では
# 整数の掛け算で比べる。
SQUARE_RATIO_NUMERATOR = 11
SQUARE_RATIO_DENOMINATOR = 10
SQUARE_MAX_RATIO = SQUARE_RATIO_NUMERATOR / SQUARE_RATIO_DENOMINATOR

# 透過を `params.background` で確かに判定できるプロバイダー。フロントの
# `TRANSPARENT_PROVIDERS` と同じ。
TRANSPARENT_PROVIDERS = ("openai", "fake")


def classify_shape(width: int | None, height: int | None) -> HistoryShape | None:
    """幅と高さから形を決める。分からなければ None(SQL の条件と同じ規則)。"""
    if not width or not height or width <= 0 or height <= 0:
        return None
    if (
        width * SQUARE_RATIO_DENOMINATOR <= height * SQUARE_RATIO_NUMERATOR
        and height * SQUARE_RATIO_DENOMINATOR <= width * SQUARE_RATIO_NUMERATOR
    ):
        return "square"
    return "portrait" if height > width else "wide"


def asset_shape_condition(shape: HistoryShape) -> ColumnElement[bool]:
    """Asset の実寸がその形である条件。"""
    w, h = Asset.width, Asset.height
    num, den = SQUARE_RATIO_NUMERATOR, SQUARE_RATIO_DENOMINATOR
    if shape == "square":
        return and_(w * den <= h * num, h * den <= w * num)
    if shape == "portrait":
        return h * den > w * num
    return w * den > h * num


def run_has_output_shape(shape: HistoryShape) -> ColumnElement[bool]:
    """Run が、削除されていない出力のうちどれかがその形である条件。"""
    runs = select(Asset.produced_by_run_id).where(
        Asset.produced_by_run_id.is_not(None),
        Asset.deleted_at.is_(None),
        asset_shape_condition(shape),
    )
    return Run.id.in_(runs)


def run_transparent() -> ColumnElement[bool]:
    """背景を透過で頼んだ Run の条件(判定できるプロバイダーだけ)。"""
    return and_(
        Run.provider.in_(TRANSPARENT_PROVIDERS),
        Run.params["background"].as_string() == "transparent",
    )


def run_has_regions() -> ColumnElement[bool]:
    """領域の四角が1つ以上ある Run の条件。先頭の四角があるかで見る(SQLite の `json_extract` と
    PostgreSQL の `#>>` のどちらでも、無ければ NULL になる)。"""
    return Run.regions[("regions", 0)].as_string().is_not(None)


@dataclass(frozen=True)
class HistoryLabelFilter:
    """形・透過・領域の絞り込み。どれも既定は絞らない。"""

    shape: HistoryShape | None = None
    transparent: bool = False
    regions: bool = False

    def _run_attribute_conditions(self) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = []
        if self.transparent:
            conditions.append(run_transparent())
        if self.regions:
            conditions.append(run_has_regions())
        return conditions

    def run_conditions(self) -> list[ColumnElement[bool]]:
        """Run 単位の一覧(`GET /api/runs`)の条件。形は出力のどれかで見る。"""
        conditions = self._run_attribute_conditions()
        if self.shape is not None:
            conditions.append(run_has_output_shape(self.shape))
        return conditions

    def asset_conditions(self) -> list[ColumnElement[bool]]:
        """画像単位の一覧(`GET /api/history/assets`。Run と結合して使う)の条件。
        形はその画像で見る。"""
        conditions = self._run_attribute_conditions()
        if self.shape is not None:
            conditions.append(asset_shape_condition(self.shape))
        return conditions

    def scope_key(self) -> str:
        """似た画像の順のキャッシュの鍵に入れる文字列。絞らなければ空。"""
        parts = []
        if self.shape is not None:
            parts.append(f"shape={self.shape}")
        if self.transparent:
            parts.append("transparent")
        if self.regions:
            parts.append("regions")
        return ",".join(parts)

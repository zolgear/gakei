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

グループの絞り込み(含める・除く。ADR-0054 6章)も、ここに置く(`HistoryGroupFilter`)。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import ColumnElement, and_, exists, not_, or_, select, true
from sqlalchemy.orm import aliased

from app.auth.identity import CurrentUser
from app.domain.models import Asset, AssetGroup, AssetGroupMember, Run
from app.domain.visibility import group_visible

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


# -- グループ(ADR-0054 6章) ---------------------------------------------------------

# 「グループなし」を表す値(`group` / `exclude_group` のクエリ)。
NO_GROUP = "none"


class InvalidGroupFilterError(ValueError):
    pass


def asset_group_column(viewer: CurrentUser, asset: Any = Asset) -> Any:
    """`asset` が入っている見えるグループの id(削除済み・見えないグループ、どこにも入っていなければ
    NULL)。ADR-0022 により所属は1つ。外側の問い合わせの `AssetGroup` と混ざらないよう別名で引く。"""
    group = aliased(AssetGroup)
    member = aliased(AssetGroupMember)
    return (
        select(group.id)
        .join(member, member.asset_group_id == group.id)
        .where(
            member.asset_id == asset.id, group.deleted_at.is_(None), group_visible(viewer, group)
        )
        .correlate(asset)
        .scalar_subquery()
    )


def run_group_column(viewer: CurrentUser) -> Any:
    """Run の出力を入れる先に選んだグループ(`run.asset_group_id`。見えて削除されていないもの)。"""
    group = aliased(AssetGroup)
    return (
        select(group.id)
        .where(
            group.id == Run.asset_group_id, group.deleted_at.is_(None), group_visible(viewer, group)
        )
        .correlate(Run)
        .scalar_subquery()
    )


@dataclass(frozen=True)
class HistoryGroupFilter:
    """グループで含める・除く。どちらも空なら絞らない。`*_none` は「グループなし」。

    - 含める: どれかに当たるものだけ。除く: どれかに当たるものを外す。両方あれば両方を満たすもの。
    - 画像のグループは、その画像が入っている見えるグループ(`asset_group_column`)。
    - Run は「削除されていない出力のどれかが当たる」。出力の無い Run(失敗・待機など)は、出力を
      入れる先に選んだグループ(`run_group_column`)で見る。
    """

    include: tuple[uuid.UUID, ...] = ()
    include_none: bool = False
    exclude: tuple[uuid.UUID, ...] = ()
    exclude_none: bool = False

    @classmethod
    def parse(cls, include: Sequence[str] = (), exclude: Sequence[str] = ()) -> HistoryGroupFilter:
        """クエリの値(グループの id か `none`)から作る。読めない値は `InvalidGroupFilterError`。"""

        def split(values: Sequence[str]) -> tuple[tuple[uuid.UUID, ...], bool]:
            ids: list[uuid.UUID] = []
            none = False
            for value in values:
                if value == NO_GROUP:
                    none = True
                    continue
                try:
                    ids.append(uuid.UUID(value))
                except ValueError as e:
                    raise InvalidGroupFilterError(value) from e
            return tuple(sorted(set(ids))), none

        include_ids, include_none = split(include)
        exclude_ids, exclude_none = split(exclude)
        return cls(include_ids, include_none, exclude_ids, exclude_none)

    @property
    def active(self) -> bool:
        return bool(self.include or self.include_none or self.exclude or self.exclude_none)

    @property
    def includes(self) -> bool:
        return bool(self.include or self.include_none)

    def matches(self, group_id: Any) -> ColumnElement[bool]:
        """グループの id の列(NULL は「グループなし」)が当たる条件。NULL の比較は明示的に扱う。"""
        conditions: list[ColumnElement[bool]] = []
        if self.includes:
            options: list[ColumnElement[bool]] = []
            if self.include:
                options.append(and_(group_id.is_not(None), group_id.in_(self.include)))
            if self.include_none:
                options.append(group_id.is_(None))
            conditions.append(or_(*options))
        if self.exclude:
            conditions.append(or_(group_id.is_(None), not_(group_id.in_(self.exclude))))
        if self.exclude_none:
            conditions.append(group_id.is_not(None))
        return and_(*conditions) if conditions else true()

    def asset_conditions(self, viewer: CurrentUser) -> list[ColumnElement[bool]]:
        """画像単位の一覧(`Asset` を対象にする問い合わせ)の条件。"""
        if not self.active:
            return []
        return [self.matches(asset_group_column(viewer))]

    def run_conditions(self, viewer: CurrentUser) -> list[ColumnElement[bool]]:
        """Run 単位の一覧の条件。"""
        if not self.active:
            return []
        output = aliased(Asset)
        live_output = and_(output.produced_by_run_id == Run.id, output.deleted_at.is_(None))
        output_matches = exists().where(
            live_output, self.matches(asset_group_column(viewer, output))
        )
        no_output = not_(exists().where(live_output))
        return [or_(output_matches, and_(no_output, self.matches(run_group_column(viewer))))]

    def scope_key(self) -> str:
        """似た画像の順のキャッシュの鍵に入れる文字列。絞らなければ空。"""
        if not self.active:
            return ""
        include = [str(g) for g in self.include] + ([NO_GROUP] if self.include_none else [])
        exclude = [str(g) for g in self.exclude] + ([NO_GROUP] if self.exclude_none else [])
        return f"group={'|'.join(include)};exclude_group={'|'.join(exclude)}"

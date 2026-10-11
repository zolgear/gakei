"""プロンプトテンプレート(実験)の組み立て。

プロンプトを「スロット(枠)」に分け、スロットごとに固定・差し替え・ランダムができるようにする。
さらに、複数のスロットを同時に埋める「組み合わせ」で、意味の辻褄が合った入れ替えを可能にする。

組み立て(`build`)は DB もセッションも触らない純粋関数で、`TemplateSpec`(DB から読んだ写し)と
`Selections`(利用者の選択)だけを受ける。乱数は `rng`(`random.Random`)で注入できるので、
テストでは種を固定して決定的に確かめられる。

候補のテキストに含まれる `{a|b}`、`<lora:...>`、`(tag:1.2)` などは解釈も展開もしない
(SD WebUI 側の拡張に任せる)。
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    PromptCombo,
    PromptComboItem,
    PromptSlot,
    PromptTemplate,
    PromptVariant,
)
from app.i18n import t

# スロットの解決のしかた。`combo` は組み合わせが埋めたことを表す(選択としては指定できない)。
MODE_FIXED = "fixed"
MODE_RANDOM = "random"
MODE_OFF = "off"
MODE_TEXT = "text"
MODE_COMBO = "combo"

# スロットの `default_mode` に置ける値。
DEFAULT_MODES = (MODE_RANDOM, MODE_OFF)

SlotMode = Literal["fixed", "random", "off", "text"]
DefaultMode = Literal["random", "off"]


class PromptBuildError(ValueError):
    """組み立てを断る理由。API 層では 422、Run の作成では `invalid` に変換する。"""


# -- DB から読んだ写し(組み立てはこれだけを見る) ----------------------------


@dataclass(frozen=True)
class VariantSpec:
    id: uuid.UUID
    name: str | None
    text: str
    weight: float = 1.0


@dataclass(frozen=True)
class SlotSpec:
    id: uuid.UUID
    name: str
    default_mode: str = MODE_RANDOM
    variants: tuple[VariantSpec, ...] = ()


@dataclass(frozen=True)
class ComboSpec:
    id: uuid.UUID
    name: str
    weight: float = 1.0
    variant_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class TemplateSpec:
    id: uuid.UUID
    name: str
    separator: str = ", "
    # position 順(呼び出し側で並べて渡す)。
    slots: tuple[SlotSpec, ...] = ()
    combos: tuple[ComboSpec, ...] = ()


# -- 利用者の選択 ------------------------------------------------------------


@dataclass(frozen=True)
class ComboSelection:
    """`{"id": ...}` でその組み合わせ、`{"random": true}` でまだ空いている組から1つ。"""

    id: uuid.UUID | None = None
    random: bool = False


@dataclass(frozen=True)
class SlotSelection:
    mode: str
    variant_id: uuid.UUID | None = None
    text: str | None = None


@dataclass(frozen=True)
class Selections:
    combos: tuple[ComboSelection, ...] = ()
    slots: dict[uuid.UUID, SlotSelection] = field(default_factory=dict)


@dataclass(frozen=True)
class BuildResult:
    prompt: str
    # `run.prompt_build` にそのまま入れる内訳。
    build: dict[str, Any]


# -- 重み付きランダム --------------------------------------------------------


def _weighted_pick[T](items: list[tuple[T, float]], rng: random.Random) -> T | None:
    """重み付きで1つ選ぶ。重みが 0 以下のものは選ばない。

    すべての重みが 0 以下なら、重みの指定が無いのと同じと見て一様に選ぶ(候補があるのに
    何も選べない、という分かりにくいふるまいを避ける)。
    """
    if not items:
        return None
    positive = [(value, weight) for value, weight in items if weight > 0]
    if not positive:
        return items[rng.randrange(len(items))][0]
    total = sum(weight for _, weight in positive)
    threshold = rng.random() * total
    upto = 0.0
    for value, weight in positive:
        upto += weight
        if threshold < upto:
            return value
    return positive[-1][0]


# -- 組み立て ----------------------------------------------------------------


def build(
    spec: TemplateSpec,
    selections: Selections | None = None,
    *,
    rng: random.Random | None = None,
) -> BuildResult:
    """選択からプロンプト(と内訳)を組み立てる。乱数は `rng` で注入できる。"""
    selections = selections or Selections()
    rng = rng or random.Random()

    slot_by_id = {slot.id: slot for slot in spec.slots}
    # 候補 id → (スロット, 候補)。
    owner: dict[uuid.UUID, tuple[SlotSpec, VariantSpec]] = {
        variant.id: (slot, variant) for slot in spec.slots for variant in slot.variants
    }

    locked, used_combos = _resolve_combos(spec, selections, owner, rng)

    # 組み合わせが覆ったスロットは `slots` の指定を受け付けない(画面側でもロックして出す)。
    for slot_id in selections.slots:
        if slot_id in locked:
            raise PromptBuildError(
                t("promptTemplates.slotLockedByCombo", name=slot_by_id[slot_id].name)
            )
        if slot_id not in slot_by_id:
            raise PromptBuildError(t("promptTemplates.unknownSlot", id=slot_id))

    entries: list[dict[str, Any]] = []
    for slot in spec.slots:
        entries.append(_resolve_slot(slot, locked, selections.slots.get(slot.id), rng))

    # 空のスロットは区切りごと飛ばす。
    texts = [entry["text"] for entry in entries if entry["text"] and entry["text"].strip()]

    return BuildResult(
        prompt=spec.separator.join(texts),
        build={
            "template_id": str(spec.id),
            "template_name": spec.name,
            "separator": spec.separator,
            "combos": [{"id": str(combo.id), "name": combo.name} for combo in used_combos],
            "slots": entries,
        },
    )


def _resolve_combos(
    spec: TemplateSpec,
    selections: Selections,
    owner: dict[uuid.UUID, tuple[SlotSpec, VariantSpec]],
    rng: random.Random,
) -> tuple[dict[uuid.UUID, tuple[VariantSpec, ComboSpec]], list[ComboSpec]]:
    """`combos` を先頭から解決して、スロット → (候補, 組み合わせ)の割当を作る。"""
    combo_by_id = {combo.id: combo for combo in spec.combos}
    locked: dict[uuid.UUID, tuple[VariantSpec, ComboSpec]] = {}
    used: list[ComboSpec] = []

    def _apply(combo: ComboSpec) -> None:
        for variant_id in combo.variant_ids:
            found = owner.get(variant_id)
            if found is None:
                # 置換で候補が消えた組み合わせ。黙って飛ばさず断る(辻褄が崩れるため)。
                raise PromptBuildError(t("promptTemplates.comboVariantMissing", name=combo.name))
            slot, variant = found
            if slot.id in locked:
                raise PromptBuildError(
                    t("promptTemplates.comboOverlap", name=combo.name, slot=slot.name)
                )
            locked[slot.id] = (variant, combo)
        used.append(combo)

    for wanted in selections.combos:
        if wanted.id is not None:
            combo = combo_by_id.get(wanted.id)
            if combo is None:
                raise PromptBuildError(t("promptTemplates.unknownCombo", id=wanted.id))
            _apply(combo)
            continue
        if not wanted.random:
            continue
        # まだ埋まっていないスロットだけを覆う組み合わせから、重みに従って1つ。該当が
        # 無ければ何もしない(エラーにしない)。
        candidates: list[tuple[ComboSpec, float]] = []
        for combo in spec.combos:
            if combo in used:
                continue
            slot_ids = [owner[v][0].id for v in combo.variant_ids if v in owner]
            if len(slot_ids) != len(combo.variant_ids) or not slot_ids:
                continue
            if any(slot_id in locked for slot_id in slot_ids):
                continue
            candidates.append((combo, combo.weight))
        picked = _weighted_pick(candidates, rng)
        if picked is not None:
            _apply(picked)

    return locked, used


def _resolve_slot(
    slot: SlotSpec,
    locked: dict[uuid.UUID, tuple[VariantSpec, ComboSpec]],
    selection: SlotSelection | None,
    rng: random.Random,
) -> dict[str, Any]:
    """1つのスロットを解決して、`prompt_build` の `slots[]` の1件を返す。"""

    def _entry(mode: str, variant: VariantSpec | None, text: str) -> dict[str, Any]:
        return {
            "slot_id": str(slot.id),
            "name": slot.name,
            "mode": mode,
            "variant_id": None if variant is None else str(variant.id),
            "variant_name": None if variant is None else variant.name,
            "text": text,
        }

    found = locked.get(slot.id)
    if found is not None:
        variant, _combo = found
        return _entry(MODE_COMBO, variant, variant.text)

    mode = slot.default_mode if selection is None else selection.mode
    if mode == MODE_OFF:
        return _entry(MODE_OFF, None, "")
    if mode == MODE_TEXT:
        if selection is None or selection.text is None:
            raise PromptBuildError(t("promptTemplates.textRequired", name=slot.name))
        return _entry(MODE_TEXT, None, selection.text)
    if mode == MODE_FIXED:
        if selection is None or selection.variant_id is None:
            raise PromptBuildError(t("promptTemplates.variantRequired", name=slot.name))
        variant = next((v for v in slot.variants if v.id == selection.variant_id), None)
        if variant is None:
            raise PromptBuildError(
                t("promptTemplates.variantNotInSlot", id=selection.variant_id, name=slot.name)
            )
        return _entry(MODE_FIXED, variant, variant.text)
    if mode == MODE_RANDOM:
        variant = _weighted_pick([(v, v.weight) for v in slot.variants], rng)
        # 候補が無ければ空(エラーにしない)。
        if variant is None:
            return _entry(MODE_RANDOM, None, "")
        return _entry(MODE_RANDOM, variant, variant.text)
    raise PromptBuildError(t("promptTemplates.unknownMode", mode=mode))


# -- DB から写しを読む -------------------------------------------------------


def active_slots(db: Session, template_id: uuid.UUID) -> list[PromptSlot]:
    return list(
        db.execute(
            select(PromptSlot)
            .where(PromptSlot.template_id == template_id, PromptSlot.deleted_at.is_(None))
            .order_by(PromptSlot.position, PromptSlot.id)
        )
        .scalars()
        .all()
    )


def active_variants(db: Session, slot_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PromptVariant]]:
    """スロット id → 候補(position 順)。削除済みは含めない。"""
    result: dict[uuid.UUID, list[PromptVariant]] = {slot_id: [] for slot_id in slot_ids}
    if not slot_ids:
        return result
    rows = (
        db.execute(
            select(PromptVariant)
            .where(
                PromptVariant.slot_id.in_(slot_ids),
                PromptVariant.deleted_at.is_(None),
            )
            .order_by(PromptVariant.position, PromptVariant.id)
        )
        .scalars()
        .all()
    )
    for row in rows:
        result[row.slot_id].append(row)
    return result


def active_combos(db: Session, template_id: uuid.UUID) -> list[PromptCombo]:
    return list(
        db.execute(
            select(PromptCombo)
            .where(PromptCombo.template_id == template_id, PromptCombo.deleted_at.is_(None))
            .order_by(PromptCombo.position, PromptCombo.id)
        )
        .scalars()
        .all()
    )


def variant_order(
    slots: list[PromptSlot], variants: dict[uuid.UUID, list[PromptVariant]]
) -> dict[uuid.UUID, tuple[int, int]]:
    """候補 id → (スロットの順, 候補の順)。組み合わせの中身を並べるのに使う。"""
    return {
        variant.id: (slot_index, variant_index)
        for slot_index, slot in enumerate(slots)
        for variant_index, variant in enumerate(variants.get(slot.id, []))
    }


def combo_items(
    db: Session,
    combo_ids: list[uuid.UUID],
    order: dict[uuid.UUID, tuple[int, int]] | None = None,
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """組み合わせ id → 指している候補 id。

    `prompt_combo_item` は並び順の列を持たないので、`order`(`variant_order`)を渡すと
    スロットの順・候補の順に並べ、渡さなければ候補 id の順に並べる(どちらも決定的)。
    `order` に無い候補(削除済み)は落とす。
    """
    result: dict[uuid.UUID, list[uuid.UUID]] = {combo_id: [] for combo_id in combo_ids}
    if not combo_ids:
        return result
    rows = db.execute(
        select(PromptComboItem.combo_id, PromptComboItem.variant_id).where(
            PromptComboItem.combo_id.in_(combo_ids)
        )
    ).all()
    for combo_id, variant_id in rows:
        if order is not None and variant_id not in order:
            continue
        result[combo_id].append(variant_id)
    for variant_ids in result.values():
        if order is None:
            variant_ids.sort(key=str)
        else:
            variant_ids.sort(key=lambda variant_id: order[variant_id])
    return result


def load_spec(db: Session, template: PromptTemplate) -> TemplateSpec:
    """組み立てに渡す写しを DB から読む(削除済みのスロット・候補・組み合わせは含めない)。"""
    slots = active_slots(db, template.id)
    variants = active_variants(db, [slot.id for slot in slots])
    combos = active_combos(db, template.id)
    # 削除済みの候補を指している items は落とす(組み合わせ自体は残る)。
    items = combo_items(db, [combo.id for combo in combos], variant_order(slots, variants))
    return TemplateSpec(
        id=template.id,
        name=template.name,
        separator=template.separator,
        slots=tuple(
            SlotSpec(
                id=slot.id,
                name=slot.name,
                default_mode=slot.default_mode,
                variants=tuple(
                    VariantSpec(id=v.id, name=v.name, text=v.text, weight=v.weight)
                    for v in variants[slot.id]
                ),
            )
            for slot in slots
        ),
        combos=tuple(
            ComboSpec(
                id=combo.id,
                name=combo.name,
                weight=combo.weight,
                variant_ids=tuple(items[combo.id]),
            )
            for combo in combos
        ),
    )

"""プロンプトテンプレート(実験)。プロンプトを「スロット(枠)」に分けた組み立ての雛形。

証跡ではないので名前も中身も更新でき、削除は論理削除。`run` との外部キーは張らない。
件数が少ない前提でページングしない(プロンプトセット・パラメーターセットと同じ)。

PUT は丸ごと置換。id を渡した子(スロット・候補・組み合わせ)は残して更新し、本文に無い子は
論理削除、id の無い子は新しく作る。組み合わせが指す候補は、同じリクエストで作る候補を
`variant_ref`(候補の `ref`。一時キー)で指せる。

ADR-0025: 認証モードでは本人が作ったものだけを扱う。他人のテンプレートは存在しないものと
同じ 404。
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.deps import require_user
from app.auth.identity import CurrentUser
from app.deps import get_session
from app.domain import prompt_templates as pt
from app.domain.models import (
    PromptCombo,
    PromptComboItem,
    PromptSlot,
    PromptTemplate,
    PromptVariant,
)
from app.domain.schemas import (
    PromptBuildRequest,
    PromptBuildResponse,
    PromptComboInput,
    PromptComboResponse,
    PromptSlotInput,
    PromptSlotResponse,
    PromptTemplateListResponse,
    PromptTemplateResponse,
    PromptTemplateSummary,
    PromptTemplateWriteRequest,
    PromptVariantResponse,
)
from app.domain.visibility import get_visible_prompt_template, prompt_template_visible
from app.i18n import t

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt-templates"])


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _get_active(db: Session, template_id: uuid.UUID, user: CurrentUser) -> PromptTemplate:
    template = get_visible_prompt_template(db, user, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=t("promptTemplates.notFound"))
    return template


# -- レスポンスの組み立て ----------------------------------------------------


def _to_response(db: Session, template: PromptTemplate) -> PromptTemplateResponse:
    slots = pt.active_slots(db, template.id)
    variants = pt.active_variants(db, [s.id for s in slots])
    combos = pt.active_combos(db, template.id)
    # 削除済みの候補は含めず、スロットの順・候補の順に並べる。
    items = pt.combo_items(db, [c.id for c in combos], pt.variant_order(slots, variants))
    return PromptTemplateResponse(
        id=template.id,
        name=template.name,
        separator=template.separator,
        created_at=template.created_at,
        updated_at=template.updated_at,
        slots=[
            PromptSlotResponse(
                id=slot.id,
                name=slot.name,
                default_mode=slot.default_mode,  # type: ignore[arg-type]
                position=slot.position,
                variants=[
                    PromptVariantResponse(
                        id=v.id, name=v.name, text=v.text, weight=v.weight, position=v.position
                    )
                    for v in variants[slot.id]
                ],
            )
            for slot in slots
        ],
        combos=[
            PromptComboResponse(
                id=combo.id,
                name=combo.name,
                weight=combo.weight,
                position=combo.position,
                variant_ids=items[combo.id],
            )
            for combo in combos
        ],
    )


# -- 書き込み(作成と丸ごと置換で共通) --------------------------------------


def _apply_slots(
    db: Session,
    template: PromptTemplate,
    slots_in: list[PromptSlotInput],
    now: datetime,
) -> tuple[dict[str, uuid.UUID], dict[uuid.UUID, uuid.UUID]]:
    """スロットと候補を本文の内容に合わせる。

    返すのは(候補の `ref` → 候補 id、候補 id → スロット id)。後者は組み合わせの検証に使う。
    """
    existing_slots = {s.id: s for s in pt.active_slots(db, template.id)}
    existing_variants = pt.active_variants(db, list(existing_slots))

    kept_slots: set[uuid.UUID] = set()
    ref_to_id: dict[str, uuid.UUID] = {}
    slot_of_variant: dict[uuid.UUID, uuid.UUID] = {}

    for position, slot_in in enumerate(slots_in):
        if slot_in.id is not None:
            slot = existing_slots.get(slot_in.id)
            if slot is None:
                raise HTTPException(
                    status_code=422,
                    detail=t("promptTemplates.unknownSlotId", id=slot_in.id),
                )
            slot.name = slot_in.name
            slot.default_mode = slot_in.default_mode
            slot.position = position
            slot.updated_at = now
        else:
            slot = PromptSlot(
                template_id=template.id,
                name=slot_in.name,
                default_mode=slot_in.default_mode,
                position=position,
                created_at=now,
                updated_at=now,
            )
            db.add(slot)
            db.flush()
        kept_slots.add(slot.id)

        current = {v.id: v for v in existing_variants.get(slot.id, [])}
        kept_variants: set[uuid.UUID] = set()
        for v_position, variant_in in enumerate(slot_in.variants):
            if variant_in.id is not None:
                variant = current.get(variant_in.id)
                if variant is None:
                    raise HTTPException(
                        status_code=422,
                        detail=t("promptTemplates.unknownVariantId", id=variant_in.id),
                    )
                variant.name = variant_in.name
                variant.text = variant_in.text
                variant.weight = variant_in.weight
                variant.position = v_position
                variant.updated_at = now
            else:
                variant = PromptVariant(
                    slot_id=slot.id,
                    name=variant_in.name,
                    text=variant_in.text,
                    weight=variant_in.weight,
                    position=v_position,
                    created_at=now,
                    updated_at=now,
                )
                db.add(variant)
                db.flush()
            kept_variants.add(variant.id)
            slot_of_variant[variant.id] = slot.id
            if variant_in.ref is not None:
                if variant_in.ref in ref_to_id:
                    raise HTTPException(
                        status_code=422,
                        detail=t("promptTemplates.duplicateRef", ref=variant_in.ref),
                    )
                ref_to_id[variant_in.ref] = variant.id

        # 本文に無かった候補は論理削除し、組み合わせからも外す。
        for variant_id, variant in current.items():
            if variant_id not in kept_variants:
                variant.deleted_at = now
                variant.updated_at = now
                db.execute(delete(PromptComboItem).where(PromptComboItem.variant_id == variant_id))

    # 本文に無かったスロットは、その候補ごと論理削除する。
    for slot_id, slot in existing_slots.items():
        if slot_id in kept_slots:
            continue
        slot.deleted_at = now
        slot.updated_at = now
        for variant in existing_variants.get(slot_id, []):
            variant.deleted_at = now
            variant.updated_at = now
            db.execute(delete(PromptComboItem).where(PromptComboItem.variant_id == variant.id))

    return ref_to_id, slot_of_variant


def _apply_combos(
    db: Session,
    template: PromptTemplate,
    combos_in: list[PromptComboInput],
    ref_to_id: dict[str, uuid.UUID],
    slot_of_variant: dict[uuid.UUID, uuid.UUID],
    now: datetime,
) -> None:
    existing = {c.id: c for c in pt.active_combos(db, template.id)}
    kept: set[uuid.UUID] = set()

    for position, combo_in in enumerate(combos_in):
        if combo_in.id is not None:
            combo = existing.get(combo_in.id)
            if combo is None:
                raise HTTPException(
                    status_code=422,
                    detail=t("promptTemplates.unknownComboId", id=combo_in.id),
                )
            combo.name = combo_in.name
            combo.weight = combo_in.weight
            combo.position = position
            combo.updated_at = now
        else:
            combo = PromptCombo(
                template_id=template.id,
                name=combo_in.name,
                weight=combo_in.weight,
                position=position,
                created_at=now,
                updated_at=now,
            )
            db.add(combo)
            db.flush()
        kept.add(combo.id)

        # 指す候補を解決して、同じスロットの候補を2つ以上指していないかを確かめる。
        variant_ids: list[uuid.UUID] = []
        seen_slots: dict[uuid.UUID, uuid.UUID] = {}
        for item_in in combo_in.items:
            if item_in.variant_ref is not None:
                variant_id = ref_to_id.get(item_in.variant_ref)
                if variant_id is None:
                    raise HTTPException(
                        status_code=422,
                        detail=t("promptTemplates.unknownVariantRef", ref=item_in.variant_ref),
                    )
            else:
                assert item_in.variant_id is not None  # スキーマでどちらか一方を強制している
                variant_id = item_in.variant_id
            slot_id = slot_of_variant.get(variant_id)
            if slot_id is None:
                raise HTTPException(
                    status_code=422,
                    detail=t("promptTemplates.variantNotInTemplate", id=variant_id),
                )
            if slot_id in seen_slots:
                slot = db.get(PromptSlot, slot_id)
                raise HTTPException(
                    status_code=422,
                    detail=t(
                        "promptTemplates.comboSameSlot",
                        name=combo_in.name,
                        slot="" if slot is None else slot.name,
                    ),
                )
            seen_slots[slot_id] = variant_id
            variant_ids.append(variant_id)

        # 中身は入れ替える(論理削除を持たない表なので、いったん消して入れ直す)。
        db.execute(delete(PromptComboItem).where(PromptComboItem.combo_id == combo.id))
        for variant_id in variant_ids:
            db.add(PromptComboItem(combo_id=combo.id, variant_id=variant_id))

    for combo_id, combo in existing.items():
        if combo_id not in kept:
            combo.deleted_at = now
            combo.updated_at = now
            db.execute(delete(PromptComboItem).where(PromptComboItem.combo_id == combo_id))


def _write(
    db: Session, template: PromptTemplate, body: PromptTemplateWriteRequest, now: datetime
) -> None:
    template.name = body.name
    template.separator = body.separator
    template.updated_at = now
    ref_to_id, slot_of_variant = _apply_slots(db, template, body.slots, now)
    _apply_combos(db, template, body.combos, ref_to_id, slot_of_variant, now)


# -- エンドポイント ----------------------------------------------------------


@router.get("", response_model=PromptTemplateListResponse, operation_id="list_prompt_templates")
def list_prompt_templates(
    db: Session = Depends(get_session), user: CurrentUser = Depends(require_user)
) -> PromptTemplateListResponse:
    templates = (
        db.execute(
            select(PromptTemplate)
            .where(PromptTemplate.deleted_at.is_(None), prompt_template_visible(user))
            .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
        )
        .scalars()
        .all()
    )
    items: list[PromptTemplateSummary] = []
    for template in templates:
        slots = pt.active_slots(db, template.id)
        variants = pt.active_variants(db, [s.id for s in slots])
        items.append(
            PromptTemplateSummary(
                id=template.id,
                name=template.name,
                separator=template.separator,
                slot_count=len(slots),
                variant_count=sum(len(rows) for rows in variants.values()),
                combo_count=len(pt.active_combos(db, template.id)),
                created_at=template.created_at,
                updated_at=template.updated_at,
            )
        )
    return PromptTemplateListResponse(items=items)


@router.post(
    "",
    response_model=PromptTemplateResponse,
    status_code=201,
    operation_id="create_prompt_template",
)
def create_prompt_template(
    body: PromptTemplateWriteRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptTemplateResponse:
    now = _utcnow()
    template = PromptTemplate(
        name=body.name,
        separator=body.separator,
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(template)
    db.flush()
    _write(db, template, body, now)
    db.commit()
    return _to_response(db, template)


@router.get(
    "/{template_id}", response_model=PromptTemplateResponse, operation_id="get_prompt_template"
)
def get_prompt_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptTemplateResponse:
    return _to_response(db, _get_active(db, template_id, user))


@router.put(
    "/{template_id}", response_model=PromptTemplateResponse, operation_id="replace_prompt_template"
)
def replace_prompt_template(
    template_id: uuid.UUID,
    body: PromptTemplateWriteRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptTemplateResponse:
    template = _get_active(db, template_id, user)
    _write(db, template, body, _utcnow())
    db.commit()
    return _to_response(db, template)


@router.delete("/{template_id}", status_code=204, operation_id="delete_prompt_template")
def delete_prompt_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> None:
    template = _get_active(db, template_id, user)
    template.deleted_at = _utcnow()
    db.commit()


@router.post(
    "/{template_id}/build",
    response_model=PromptBuildResponse,
    operation_id="build_prompt_template",
)
def build_prompt_template(
    template_id: uuid.UUID,
    body: PromptBuildRequest,
    db: Session = Depends(get_session),
    user: CurrentUser = Depends(require_user),
) -> PromptBuildResponse:
    """選択を渡して組み立て結果を返す(生成はしない。確認用)。"""
    template = _get_active(db, template_id, user)
    spec = pt.load_spec(db, template)
    rng = random.Random(body.seed) if body.seed is not None else None
    try:
        result = pt.build(spec, body.selections.to_domain(), rng=rng)
    except pt.PromptBuildError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return PromptBuildResponse(prompt=result.prompt, build=result.build)

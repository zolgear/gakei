/**
 * プロンプトテンプレート(実験)の編集のダイアログ。実験として触れればよい最小の作り。
 *
 * 区切り文字・スロット(名前・行き先・既定の挙動・並べ替え・削除)・候補(名前・テキスト・重み・
 * 並べ替え・削除)・組み合わせ(名前・重み・どの候補を指すか)を1つの下書き(`TemplateDraft`)で
 * 持ち、保存は `PUT`(丸ごと置換)で送る。残したい子は id を付けて本文に入れる(`draftToWriteRequest`)。
 *
 * 見た目は、スロットと組み合わせを見出し帯つきのカードにし、その中に候補を表のように並べる。
 * 候補の列見出しはスロットごとに1回だけ出し、各行の入力欄は `aria-label` で何の欄かを示す。
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ApiError, getPromptTemplate, replacePromptTemplate, type PromptTemplateResponse } from '../../api/client'
import { Modal } from '../../components/Modal'
import { fmt, useI18n } from '../../i18n'
import {
  draftBlockReasons,
  draftToWriteRequest,
  indexDraftBlockReasons,
  moveItem,
  newComboDraft,
  newSlotDraft,
  newVariantDraft,
  nextUnusedNumber,
  templateToDraft,
  variantLabel,
  type ComboDraft,
  type DraftBlockReason,
  type SlotDraft,
  type TemplateDraft,
  type VariantDraft,
} from './promptTemplates'
import styles from './PromptTemplates.module.css'

interface PromptTemplateEditorProps {
  /** 編集するテンプレート(null なら閉じている)。 */
  templateId: string | null
  onClose: () => void
  onSaved: (template: PromptTemplateResponse) => void
}

export function PromptTemplateEditor({ templateId, onClose, onSaved }: PromptTemplateEditorProps) {
  const { t } = useI18n()
  const e = t.promptTemplates.editor

  const query = useQuery({
    queryKey: ['prompt-template', templateId],
    queryFn: () => getPromptTemplate(templateId as string),
    enabled: templateId !== null,
  })
  const template = query.data?.id === templateId ? query.data : undefined

  return (
    <Modal open={templateId !== null} title={e.title} onClose={onClose} size="large">
      {template === undefined && query.isError && <p className={styles.placeholder}>{e.loadFailed}</p>}
      {template === undefined && !query.isError && <p className={styles.placeholder}>{e.loading}</p>}
      {/* 下書きの初期値はマウント時にしか読まないので、開き直す・保存し直すたびに作り直す。 */}
      {template !== undefined && (
        <EditorBody
          key={`${template.id}:${template.updated_at}`}
          template={template}
          onClose={onClose}
          onSaved={onSaved}
        />
      )}
    </Modal>
  )
}

interface EmptyActionProps {
  /** 控えめな状況の説明(「候補がありません」など)。 */
  text: string
  /** 次にする操作(「候補を追加」など)。ボタン全体がこの操作になる。 */
  cta: string
  onClick: () => void
}

/** 空のときの見せ方。素の文で終わらせず、そのまま次の操作に繋げる。 */
function EmptyAction({ text, cta, onClick }: EmptyActionProps) {
  return (
    <button type="button" className={styles.emptyAction} onClick={onClick}>
      <span className={styles.emptyActionText}>{text}</span>
      <span className={styles.emptyActionCta}>{cta}</span>
    </button>
  )
}

interface EditorBodyProps {
  template: PromptTemplateResponse
  onClose: () => void
  onSaved: (template: PromptTemplateResponse) => void
}

function EditorBody({ template, onClose, onSaved }: EditorBodyProps) {
  const { t } = useI18n()
  const e = t.promptTemplates.editor
  const [draft, setDraft] = useState<TemplateDraft>(() => templateToDraft(template))
  const [error, setError] = useState<string | null>(null)
  /**
   * 入力欄の実体。要約を押したとき、問題のある欄へフォーカスを移してスクロールするのに使う。
   * キーは `fieldId()` が作る。
   */
  const fieldRefs = useRef(new Map<string, HTMLElement>())
  /** 要約を押して最後に移った欄のキー。次に押したときは、ここの次の問題へ進む。 */
  const jumpCursor = useRef<string | null>(null)

  const saveMutation = useMutation({
    mutationFn: (next: TemplateDraft) => replacePromptTemplate(template.id, draftToWriteRequest(next)),
    onSuccess: (saved) => {
      setError(null)
      onSaved(saved)
      onClose()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : e.saveFailed),
  })

  function patch(next: Partial<TemplateDraft>) {
    setDraft((prev) => ({ ...prev, ...next }))
  }

  function patchSlot(index: number, next: Partial<SlotDraft>) {
    setDraft((prev) => ({ ...prev, slots: prev.slots.map((s, i) => (i === index ? { ...s, ...next } : s)) }))
  }

  function patchVariant(slotIndex: number, variantIndex: number, next: Partial<VariantDraft>) {
    setDraft((prev) => ({
      ...prev,
      slots: prev.slots.map((slot, i) =>
        i === slotIndex
          ? { ...slot, variants: slot.variants.map((v, j) => (j === variantIndex ? { ...v, ...next } : v)) }
          : slot,
      ),
    }))
  }

  function patchCombo(index: number, next: Partial<ComboDraft>) {
    setDraft((prev) => ({ ...prev, combos: prev.combos.map((c, i) => (i === index ? { ...c, ...next } : c)) }))
  }

  /** 新しいスロット・組み合わせの名前(既にある名前とぶつからない連番を付ける)。 */
  function numberedName(templateText: string, existing: { name: string }[]): string {
    const label = (n: number) => fmt(templateText, { n })
    return label(nextUnusedNumber(existing.map((item) => item.name), label))
  }

  function addSlot() {
    patch({ slots: [...draft.slots, newSlotDraft(numberedName(e.newSlotName, draft.slots))] })
  }

  function addVariant(slotIndex: number) {
    patchSlot(slotIndex, { variants: [...draft.slots[slotIndex].variants, newVariantDraft()] })
  }

  function addCombo() {
    patch({ combos: [...draft.combos, newComboDraft(numberedName(e.newComboName, draft.combos))] })
  }

  const reasons = draftBlockReasons(draft)
  // 理由は入力欄のその場に出すので、下書きのキーで引ける形にしておく。
  const blocked = indexDraftBlockReasons(reasons)

  /** 入力欄を覚えるときのキー。種類ごとに、下書きのキーで1つに決まる。 */
  function fieldId(reason: DraftBlockReason): string {
    switch (reason.code) {
      case 'nameRequired':
        return 'name'
      case 'slotNameRequired':
        return `slot:${reason.slotKey}`
      case 'variantTextRequired':
        return `variant:${reason.variantKey}`
      case 'comboNameRequired':
        return `combo:${reason.comboKey}`
      case 'comboEmpty':
        return `comboItems:${reason.comboKey}`
    }
  }

  /** 入力欄を覚える ref。外れたら忘れる(並べ替え・削除で残骸が溜まらないように)。 */
  function registerField(id: string) {
    return (el: HTMLElement | null) => {
      if (el === null) fieldRefs.current.delete(id)
      else fieldRefs.current.set(id, el)
    }
  }

  /**
   * 問題のある欄を文書順に並べたもの(テンプレート名 → スロット → 候補 → 組み合わせ)。
   * 1つの欄に複数の理由が重なることはあるので、同じキーは1つにまとめる。
   */
  function invalidFields(current: DraftBlockReason[]): { id: string; el: HTMLElement }[] {
    const seen = new Set<string>()
    const fields: { id: string; el: HTMLElement }[] = []
    for (const reason of current) {
      const id = fieldId(reason)
      if (seen.has(id)) continue
      seen.add(id)
      const el = fieldRefs.current.get(id)
      if (el !== undefined) fields.push({ id, el })
    }
    return fields
  }

  /**
   * 問題のある欄へ順に移る。`fromCursor` が真なら、最後に移った欄(または今いる欄)の次へ進み、
   * 最後まで行ったら先頭に戻る。偽なら先頭の欄へ。
   */
  function focusInvalidField(current: DraftBlockReason[], fromCursor: boolean) {
    const fields = invalidFields(current)
    if (fields.length === 0) return
    let index = 0
    if (fromCursor) {
      // 要約を押すと焦点は要約のボタンに移るので、まず今いる欄、無ければ前回移った欄を起点にする。
      const active = document.activeElement
      const at =
        active instanceof Node
          ? fields.findIndex(({ el }) => el === active || el.contains(active))
          : -1
      const from = at >= 0 ? at : fields.findIndex(({ id }) => id === jumpCursor.current)
      index = from >= 0 ? (from + 1) % fields.length : 0
    }
    const { id, el } = fields[index]
    jumpCursor.current = id
    el.scrollIntoView({ block: 'nearest' })
    // 組み合わせが指す候補はラジオの集まりなので、包んでいる fieldset ではなく中の1つを狙う。
    const target = el.tagName === 'FIELDSET' ? el.querySelector<HTMLElement>('input') : el
    target?.focus({ preventScroll: true })
  }

  /**
   * 組み合わせから指せる候補を、スロットごとにまとめたもの(テキストが空の候補はまだ保存できない
   * ので出さない。指せる候補が1つも無いスロットは出さない)。
   *
   * 1つの組み合わせは同じスロットの候補を2つ以上指せないので、スロットごとのラジオで出す。
   */
  const comboSlotGroups = draft.slots
    .map((slot) => ({ slot, options: slot.variants.filter((variant) => variant.text.trim() !== '') }))
    .filter((group) => group.options.length > 0)
  // 組み合わせを追加できない理由(押せない理由をボタンのその場に出す)。
  const comboBlocked = comboSlotGroups.length === 0 ? e.comboNeedsVariant : null

  /**
   * 組み合わせの、あるスロットの選択を差し替える(`null` は「指定しない」)。そのスロットの
   * 候補の参照をいったん全部落としてから入れ直すので、同じスロットを2つ指すことにならない。
   */
  function pickComboVariant(comboIndex: number, slot: SlotDraft, variantKey: string | null) {
    const combo = draft.combos[comboIndex]
    const slotKeys = new Set(slot.variants.map((variant) => variant.key))
    const rest = combo.variantKeys.filter((key) => !slotKeys.has(key))
    patchCombo(comboIndex, { variantKeys: variantKey === null ? rest : [...rest, variantKey] })
  }

  return (
    <form
        className={styles.editor}
        onSubmit={(ev) => {
          ev.preventDefault()
          // 断るときは保存せず、先頭の問題の欄へ案内する(印は常に出ているので、ここは移動だけ)。
          if (reasons.length > 0) {
            focusInvalidField(reasons, false)
            return
          }
          saveMutation.mutate(draft)
        }}
      >
        <div className={styles.editorScroll}>
          <div className={styles.fieldRow}>
            <label className={`${styles.field} ${styles.nameField}`}>
              <span className={styles.fieldLabel}>{e.nameLabel}</span>
              <input
                ref={registerField('name')}
                className={`${styles.input} ${blocked.templateName ? styles.invalid : ''}`}
                value={draft.name}
                maxLength={100}
                aria-invalid={blocked.templateName || undefined}
                onChange={(ev) => patch({ name: ev.target.value })}
              />
              {blocked.templateName && (
                <p className={styles.errorText} role="alert">
                  {e.nameRequired}
                </p>
              )}
            </label>
            {/* 区切り文字の中身は数文字なので幅を詰め、使い方の説明はツールチップに置く。 */}
            <label className={`${styles.field} ${styles.separatorField}`} title={e.separatorHint}>
              <span className={styles.fieldLabel}>{e.separatorLabel}</span>
              <input
                className={styles.input}
                value={draft.separator}
                maxLength={20}
                title={e.separatorHint}
                onChange={(ev) => patch({ separator: ev.target.value })}
              />
            </label>
          </div>

          {/* 名前・区切り文字 → スロット → 組み合わせ の段取りが分かるよう、節の前に区切り線を置く。 */}
          <div className={`${styles.sectionHeaderRow} ${styles.sectionBreak}`}>
            <h3 className={styles.sectionHeading}>{e.slotsHeading}</h3>
            <button type="button" className={styles.smallButton} onClick={addSlot}>
              {e.addSlot}
            </button>
          </div>
          {draft.slots.length === 0 && (
            <EmptyAction text={t.promptTemplates.form.noSlots} cta={e.addSlot} onClick={addSlot} />
          )}
          {draft.slots.map((slot, slotIndex) => {
            const slotNameInvalid = blocked.slotNames.has(slot.key)
            return (
            <div key={slot.key} className={styles.card}>
              {/* スロットの見出し行(名前・既定の挙動・並べ替え・削除)。帯で候補の一覧と分ける。 */}
              <div className={styles.cardHead}>
                <label className={`${styles.headField} ${styles.headFieldGrow}`}>
                  <span className={styles.fieldLabel}>{e.slotNameLabel}</span>
                  <input
                    ref={registerField(`slot:${slot.key}`)}
                    className={`${styles.input} ${styles.headName} ${slotNameInvalid ? styles.invalid : ''}`}
                    value={slot.name}
                    maxLength={100}
                    aria-invalid={slotNameInvalid || undefined}
                    onChange={(ev) => patchSlot(slotIndex, { name: ev.target.value })}
                  />
                  {slotNameInvalid && (
                    <p className={styles.errorText} role="alert">
                      {e.nameRequired}
                    </p>
                  )}
                </label>
                <label className={styles.headField}>
                  <span className={styles.fieldLabel}>{e.defaultModeLabel}</span>
                  <select
                    className={`${styles.select} ${styles.headSelect}`}
                    value={slot.defaultMode}
                    onChange={(ev) =>
                      patchSlot(slotIndex, { defaultMode: ev.target.value as SlotDraft['defaultMode'] })
                    }
                  >
                    <option value="random">{e.defaultModeRandom}</option>
                    <option value="off">{e.defaultModeOff}</option>
                  </select>
                </label>
                <div className={styles.cardActions}>
                  <button
                    type="button"
                    className={styles.iconButton}
                    aria-label={e.moveUp}
                    title={e.moveUp}
                    disabled={slotIndex === 0}
                    onClick={() => patch({ slots: moveItem(draft.slots, slotIndex, slotIndex - 1) })}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className={styles.iconButton}
                    aria-label={e.moveDown}
                    title={e.moveDown}
                    disabled={slotIndex === draft.slots.length - 1}
                    onClick={() => patch({ slots: moveItem(draft.slots, slotIndex, slotIndex + 1) })}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className={`${styles.iconButton} ${styles.smallDanger}`}
                    aria-label={e.remove}
                    title={e.remove}
                    onClick={() => patch({ slots: draft.slots.filter((_, i) => i !== slotIndex) })}
                  >
                    ✕
                  </button>
                </div>
              </div>

              <div className={styles.cardBody}>
                {slot.variants.length === 0 ? (
                  <EmptyAction text={e.noVariants} cta={e.addVariant} onClick={() => addVariant(slotIndex)} />
                ) : (
                  <>
                    {/* 列見出しはスロットに1回だけ。各行の入力欄は aria-label で読み上げる。 */}
                    <div className={styles.variantTable}>
                      <div className={styles.variantHead} aria-hidden="true">
                        <span className={`${styles.colHead} ${styles.colName}`}>{e.variantNameLabel}</span>
                        <span className={`${styles.colHead} ${styles.colText}`}>{e.variantTextLabel}</span>
                        <span className={`${styles.colHead} ${styles.colWeight}`}>{e.weightLabel}</span>
                        <span className={styles.colActions} />
                      </div>
                      {slot.variants.map((variant, variantIndex) => {
                        // テキストは必須。印はその場に常に出す(追加した直後の案内も兼ねる)。
                        const textInvalid = blocked.variantTexts.has(variant.key)
                        return (
                        <div key={variant.key} className={styles.variantRow}>
                          <input
                            className={`${styles.input} ${styles.colName}`}
                            aria-label={e.variantNameLabel}
                            value={variant.name}
                            maxLength={100}
                            onChange={(ev) => patchVariant(slotIndex, variantIndex, { name: ev.target.value })}
                          />
                          <textarea
                            ref={registerField(`variant:${variant.key}`)}
                            className={`${styles.textarea} ${styles.variantText} ${styles.colText} ${textInvalid ? styles.invalid : ''}`}
                            aria-label={e.variantTextLabel}
                            value={variant.text}
                            rows={1}
                            aria-invalid={textInvalid || undefined}
                            onChange={(ev) => patchVariant(slotIndex, variantIndex, { text: ev.target.value })}
                          />
                          <input
                            className={`${styles.input} ${styles.colWeight}`}
                            aria-label={e.weightLabel}
                            type="number"
                            min={0}
                            max={1000}
                            step={0.1}
                            value={variant.weight}
                            onChange={(ev) => {
                              const next = Number(ev.target.value)
                              patchVariant(slotIndex, variantIndex, {
                                weight: Number.isFinite(next) && next >= 0 ? next : 0,
                              })
                            }}
                          />
                          <div className={styles.cardActions}>
                            <button
                              type="button"
                              className={styles.iconButton}
                              aria-label={e.moveUp}
                              title={e.moveUp}
                              disabled={variantIndex === 0}
                              onClick={() =>
                                patchSlot(slotIndex, {
                                  variants: moveItem(slot.variants, variantIndex, variantIndex - 1),
                                })
                              }
                            >
                              ↑
                            </button>
                            <button
                              type="button"
                              className={styles.iconButton}
                              aria-label={e.moveDown}
                              title={e.moveDown}
                              disabled={variantIndex === slot.variants.length - 1}
                              onClick={() =>
                                patchSlot(slotIndex, {
                                  variants: moveItem(slot.variants, variantIndex, variantIndex + 1),
                                })
                              }
                            >
                              ↓
                            </button>
                            <button
                              type="button"
                              className={`${styles.iconButton} ${styles.smallDanger}`}
                              aria-label={e.remove}
                              title={e.remove}
                              onClick={() =>
                                patchSlot(slotIndex, { variants: slot.variants.filter((_, j) => j !== variantIndex) })
                              }
                            >
                              ✕
                            </button>
                          </div>
                          {textInvalid && (
                            <p className={styles.variantError} role="alert">
                              {e.variantTextInvalid}
                            </p>
                          )}
                        </div>
                        )
                      })}
                    </div>
                    {/* 新しい行は末尾に増えるので、追加のボタンも一覧の下に置く。 */}
                    <div className={styles.cardFoot}>
                      <button type="button" className={styles.smallButton} onClick={() => addVariant(slotIndex)}>
                        {e.addVariant}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
            )
          })}

          <div className={`${styles.sectionHeaderRow} ${styles.sectionBreak}`}>
            <h3 className={styles.sectionHeading}>{e.combosHeading}</h3>
            {/* 押せない理由はボタンのすぐ隣に出す。disabled な要素では title が出ないブラウザが
                あるので、包んだ span 側にも付ける。 */}
            <div className={styles.headerActions}>
              {comboBlocked !== null && <span className={styles.headerHint}>{comboBlocked}</span>}
              <span title={comboBlocked ?? undefined}>
                <button
                  type="button"
                  className={styles.smallButton}
                  disabled={comboBlocked !== null}
                  title={comboBlocked ?? undefined}
                  onClick={addCombo}
                >
                  {e.addCombo}
                </button>
              </span>
            </div>
          </div>
          {comboBlocked === null && draft.combos.length === 0 && (
            <EmptyAction text={e.noCombos} cta={e.addCombo} onClick={addCombo} />
          )}
          {draft.combos.map((combo, comboIndex) => {
            // 候補を1つも指していない組み合わせは保存できない。印はその場に常に出す。
            const comboEmpty = blocked.comboEmpties.has(combo.key)
            const comboNameInvalid = blocked.comboNames.has(combo.key)
            return (
            <div key={combo.key} className={styles.card}>
              <div className={styles.cardHead}>
                <label className={`${styles.headField} ${styles.headFieldGrow}`}>
                  <span className={styles.fieldLabel}>{e.comboNameLabel}</span>
                  <input
                    ref={registerField(`combo:${combo.key}`)}
                    className={`${styles.input} ${styles.headName} ${comboNameInvalid ? styles.invalid : ''}`}
                    value={combo.name}
                    maxLength={100}
                    aria-invalid={comboNameInvalid || undefined}
                    onChange={(ev) => patchCombo(comboIndex, { name: ev.target.value })}
                  />
                  {comboNameInvalid && (
                    <p className={styles.errorText} role="alert">
                      {e.nameRequired}
                    </p>
                  )}
                </label>
                <label className={styles.headField}>
                  <span className={styles.fieldLabel}>{e.weightLabel}</span>
                  <input
                    className={`${styles.input} ${styles.headWeight}`}
                    type="number"
                    min={0}
                    max={1000}
                    step={0.1}
                    value={combo.weight}
                    onChange={(ev) => {
                      const next = Number(ev.target.value)
                      patchCombo(comboIndex, { weight: Number.isFinite(next) && next >= 0 ? next : 0 })
                    }}
                  />
                </label>
                <div className={styles.cardActions}>
                  <button
                    type="button"
                    className={styles.iconButton}
                    aria-label={e.moveUp}
                    title={e.moveUp}
                    disabled={comboIndex === 0}
                    onClick={() => patch({ combos: moveItem(draft.combos, comboIndex, comboIndex - 1) })}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className={styles.iconButton}
                    aria-label={e.moveDown}
                    title={e.moveDown}
                    disabled={comboIndex === draft.combos.length - 1}
                    onClick={() => patch({ combos: moveItem(draft.combos, comboIndex, comboIndex + 1) })}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className={`${styles.iconButton} ${styles.smallDanger}`}
                    aria-label={e.remove}
                    title={e.remove}
                    onClick={() => patch({ combos: draft.combos.filter((_, i) => i !== comboIndex) })}
                  >
                    ✕
                  </button>
                </div>
              </div>
              <div className={styles.cardBody}>
                {/* スロットごとに1つだけ、という制約はラジオで表す(そもそも2つ選べない)。
                    組み合わせは一部のスロットだけを覆えるので、先頭に「指定しない」を置く。
                    ラジオの name は組み合わせごと・スロットごとに分ける(同じにすると別の
                    組み合わせのラジオまで連動する)。1スロットを横1行に並べ、入り切らなければ折り返す。 */}
                <fieldset ref={registerField(`comboItems:${combo.key}`)} className={styles.checkList}>
                  <legend className={styles.legend}>{e.comboItemsLabel}</legend>
                  {comboSlotGroups.map(({ slot, options }) => {
                    const groupName = `combo-${combo.key}-slot-${slot.key}`
                    const picked = options.find((option) => combo.variantKeys.includes(option.key))
                    return (
                      <div key={slot.key} className={styles.comboSlotRow}>
                        <span className={styles.comboSlotName}>{slot.name}</span>
                        <div className={styles.comboOptions}>
                          <label className={styles.checkItem}>
                            <input
                              type="radio"
                              name={groupName}
                              checked={picked === undefined}
                              onChange={() => pickComboVariant(comboIndex, slot, null)}
                            />
                            {e.comboNotSet}
                          </label>
                          {options.map((option) => (
                            <label key={option.key} className={styles.checkItem}>
                              <input
                                type="radio"
                                name={groupName}
                                checked={picked?.key === option.key}
                                onChange={() => pickComboVariant(comboIndex, slot, option.key)}
                              />
                              {variantLabel(option)}
                            </label>
                          ))}
                        </div>
                      </div>
                    )
                  })}
                  {comboEmpty && (
                    <p className={styles.variantError} role="alert">
                      {e.comboEmptyInvalid}
                    </p>
                  )}
                </fieldset>
              </div>
            </div>
            )
          })}
        </div>

        {error && (
          <p className={styles.errorText} role="alert">
            {error}
          </p>
        )}
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? e.saving : e.save}
          </button>
          <button type="button" className={styles.secondary} onClick={onClose}>
            {e.close}
          </button>
          {/* 理由はその項目の場所に出すので、ここは件数に関わらず常に1行の要約だけ。スクロール
              領域の外にあるので、下まで送らなくても問題があることが分かる。押すと次の問題へ移る。 */}
          {reasons.length > 0 && (
            <button
              type="button"
              className={styles.invalidJump}
              title={e.invalidSummaryHint}
              aria-label={`${fmt(e.invalidSummary, { count: reasons.length })} / ${e.invalidSummaryHint}`}
              onClick={() => focusInvalidField(reasons, true)}
            >
              {fmt(e.invalidSummary, { count: reasons.length })}
            </button>
          )}
        </div>
    </form>
  )
}

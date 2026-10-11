/**
 * 生成フォームのテンプレートモード(実験)。プロンプト欄の代わりに出す。
 *
 * スロットごとに固定 / ランダム / 使わない / その場のテキストを選ぶ。組み合わせは
 * 「使わない / 選ぶ / ランダムに1つ引く」の3択で、「選ぶ」のときだけ一覧から複数選べる
 * (サーバーは「選んだ分 + ランダム1つ」の併用も受けるが、分かりにくいので画面では出さない)。
 * 組み合わせが覆うスロットはロックして出す(指定するとサーバーが 422 を返すため、送る
 * `selections` からも外している)。
 *
 * 組み立て結果は `POST /api/prompt-templates/{id}/build` で確かめる。実際に生成するときは
 * Run ごとにサーバーが引き直すので、ここで出るのは「引いてみた1例」。
 */
import { Fragment, useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  ApiError,
  buildPromptTemplate,
  type PromptBuildResponse,
  type PromptTemplateResponse,
} from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import {
  COMBO_MODES,
  comboFilledSlotIds,
  lockedSlotIds,
  parsePromptBuild,
  selectionBlockReasons,
  toPromptSelections,
  variantLabel,
  type ComboMode,
  type SlotMode,
  type TemplateSelectionState,
} from './promptTemplates'
import styles from './PromptTemplates.module.css'

interface PromptTemplateFormProps {
  template: PromptTemplateResponse
  selections: TemplateSelectionState
  onChange: (selections: TemplateSelectionState) => void
  /** テンプレートモードをやめる。 */
  onClear: () => void
  /** 組み立て結果をフォームへ伝える(送信できるかと参考価格の文字数に使う)。 */
  onPreview: (preview: { prompt: string } | null) => void
}

export function PromptTemplateForm({
  template,
  selections,
  onChange,
  onClear,
  onPreview,
}: PromptTemplateFormProps) {
  const { t } = useI18n()
  const f = t.promptTemplates.form
  const [build, setBuild] = useState<PromptBuildResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  // onPreview の identity が変わっても組み立てを呼び直さないよう ref で読む。
  const onPreviewRef = useRef(onPreview)
  onPreviewRef.current = onPreview

  const buildMutation = useMutation({
    mutationFn: () =>
      buildPromptTemplate(template.id, { selections: toPromptSelections(template, selections) }),
    onSuccess: (result) => {
      setError(null)
      setBuild(result)
      onPreviewRef.current({ prompt: result.prompt })
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : f.buildFailed)
      setBuild(null)
      onPreviewRef.current(null)
    },
  })
  const runBuild = buildMutation.mutate

  const blockReasons = selectionBlockReasons(template, selections, f)

  // 選択が変わるたびに組み立て直す(ランダムは引き直しになる)。連打にならないよう少し待つ。
  const selectionsKey = JSON.stringify(toPromptSelections(template, selections))
  useEffect(() => {
    if (blockReasons.length > 0) {
      onPreviewRef.current(null)
      setBuild(null)
      return
    }
    const timer = setTimeout(() => runBuild(), 200)
    return () => clearTimeout(timer)
    // selectionsKey に選択のすべてが入っている(オブジェクトの identity では走らせない)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template.id, selectionsKey, blockReasons.length, runBuild])

  // 今回の引きで組み合わせが埋めたスロットと、その組み合わせの名前。
  const filled = comboFilledSlotIds(build ?? undefined)
  const buildInfo = parsePromptBuild(build?.build)
  const pickedCombos = buildInfo?.combos ?? []
  const buildSlots = buildInfo?.slots ?? []
  // 画面でロックするスロット(ランダムの組み合わせで引かれる可能性がある分も含む)。
  const locked = lockedSlotIds(template, selections)

  function patchSlot(slotId: string, next: Partial<{ mode: SlotMode; variantId: string | null; text: string }>) {
    const current = selections.slots[slotId] ?? { mode: 'random' as SlotMode, variantId: null, text: '' }
    onChange({ ...selections, slots: { ...selections.slots, [slotId]: { ...current, ...next } } })
  }

  const combos = template.combos ?? []
  // ラジオの name。同じ画面に別のテンプレートが出ても混ざらないよう id を混ぜる。
  const comboModeName = `prompt-template-combo-mode-${template.id}`
  const comboModeLabels: Record<ComboMode, string> = {
    off: f.comboModeOff,
    pick: f.comboModePick,
    random: f.comboModeRandom,
  }

  return (
    <div className={styles.formPanel}>
      <div className={styles.formHeaderRow}>
        <h3 className={styles.formTitle}>{fmt(f.title, { name: template.name })}</h3>
        <button type="button" className={styles.smallButton} onClick={onClear}>
          {f.clear}
        </button>
      </div>

      <div className={styles.preview}>
        {/* 見出しはこの span だけ。fieldset は legend を持たず aria-label で名前を付ける
            (すぐ下のスロットの節と見た目を揃えるため)。 */}
        <span className={styles.previewLabel}>{f.combosHeading}</span>
        {combos.length === 0 ? (
          <p className={styles.hint}>{f.comboNone}</p>
        ) : (
          <fieldset className={styles.checkList} aria-label={f.combosHeading}>
            {COMBO_MODES.map((mode) => (
              <Fragment key={mode}>
                <label className={styles.checkItem}>
                  <input
                    type="radio"
                    name={comboModeName}
                    value={mode}
                    checked={selections.comboMode === mode}
                    // 「選ぶ」の選択は comboIds に残したまま持ち越す(戻せば復元される)。
                    onChange={() => onChange({ ...selections, comboMode: mode })}
                  />
                  {comboModeLabels[mode]}
                </label>
                {/* 一覧は「選ぶ」のすぐ下に出す(どの選択肢に属すかが分かるように)。 */}
                {mode === 'pick' && selections.comboMode === 'pick' && (
                  <fieldset
                    className={`${styles.checkList} ${styles.comboPickList}`}
                    aria-label={f.comboPickHeading}
                  >
                    {combos.map((combo) => (
                      <label key={combo.id} className={styles.checkItem}>
                        <input
                          type="checkbox"
                          checked={selections.comboIds.includes(combo.id)}
                          onChange={(ev) =>
                            onChange({
                              ...selections,
                              comboIds: ev.target.checked
                                ? [...selections.comboIds, combo.id]
                                : selections.comboIds.filter((id) => id !== combo.id),
                            })
                          }
                        />
                        {combo.name}
                      </label>
                    ))}
                  </fieldset>
                )}
              </Fragment>
            ))}
          </fieldset>
        )}
      </div>

      <div className={styles.preview}>
        <span className={styles.previewLabel}>{f.slotsHeading}</span>
        {(template.slots ?? []).length === 0 && <p className={styles.hint}>{f.noSlots}</p>}
        {(template.slots ?? []).map((slot) => {
          const selection = selections.slots[slot.id]
          const mode = selection?.mode ?? slot.default_mode
          const variants = slot.variants ?? []
          const isLocked = locked.has(slot.id)
          // 今回の引きで実際に埋まったときは、入った候補の名前を添える。
          const filledEntry = filled.has(slot.id)
            ? buildSlots.find((entry) => entry.slotId === slot.id)
            : undefined
          return (
            <div key={slot.id} className={styles.slotRow}>
              <span className={styles.slotName}>{slot.name}</span>
              {isLocked ? (
                <span className={styles.lockedTag}>
                  {filledEntry
                    ? fmt(f.lockedFilled, { name: filledEntry.variantName ?? filledEntry.text })
                    : f.lockedByCombo}
                </span>
              ) : (
                <div className={styles.slotControls}>
                  <select
                    className={styles.select}
                    value={mode}
                    aria-label={fmt(f.modeLabel, { name: slot.name })}
                    onChange={(ev) => patchSlot(slot.id, { mode: ev.target.value as SlotMode })}
                  >
                    <option value="fixed">{f.modeFixed}</option>
                    <option value="random">{f.modeRandom}</option>
                    <option value="off">{f.modeOff}</option>
                    <option value="text">{f.modeText}</option>
                  </select>
                  {mode === 'fixed' &&
                    (variants.length === 0 ? (
                      <span className={styles.lockedTag}>{f.noVariants}</span>
                    ) : (
                      <select
                        className={styles.select}
                        value={selection?.variantId ?? variants[0].id}
                        aria-label={fmt(f.variantLabel, { name: slot.name })}
                        onChange={(ev) => patchSlot(slot.id, { variantId: ev.target.value })}
                      >
                        {variants.map((variant) => (
                          <option key={variant.id} value={variant.id}>
                            {variantLabel(variant)}
                          </option>
                        ))}
                      </select>
                    ))}
                  {mode === 'text' && (
                    <input
                      className={styles.input}
                      value={selection?.text ?? ''}
                      placeholder={f.textPlaceholder}
                      aria-label={fmt(f.textLabel, { name: slot.name })}
                      onChange={(ev) => patchSlot(slot.id, { text: ev.target.value })}
                    />
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className={styles.formHeaderRow}>
        <span className={styles.previewLabel}>{f.previewHeading}</span>
        <button
          type="button"
          className={styles.smallButton}
          disabled={blockReasons.length > 0 || buildMutation.isPending}
          onClick={() => runBuild()}
        >
          {f.reroll}
        </button>
      </div>
      {blockReasons.map((reason) => (
        <p key={reason} className={styles.errorText}>
          {reason}
        </p>
      ))}
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}
      {buildMutation.isPending && <p className={styles.hint}>{f.building}</p>}
      {build && (
        <>
          {pickedCombos.length > 0 && (
            <p className={styles.hint}>
              {fmt(f.pickedCombos, { names: pickedCombos.map((combo) => combo.name).join(', ') })}
            </p>
          )}
          <div className={styles.preview}>
            <span className={styles.previewLabel}>{f.previewPrompt}</span>
            <p className={styles.previewText}>{build.prompt === '' ? f.previewEmpty : build.prompt}</p>
          </div>
        </>
      )}
      <p className={styles.hint}>{f.repeatHint}</p>
    </div>
  )
}

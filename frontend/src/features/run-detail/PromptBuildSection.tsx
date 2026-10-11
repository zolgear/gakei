/**
 * Run 詳細の「プロンプトテンプレート」の内訳(実験)。`run.prompt_build` があるとき、どの
 * テンプレートの、どのスロットに何が選ばれたかを出す。
 *
 * 外部キーは張っていないので、テンプレートを消した後でもこの記録だけで読める(選ばれたテキスト
 * そのものが入っている)。
 */
import { fmt, useI18n } from '../../i18n'
import { parsePromptBuild, type PromptBuildSlot } from '../prompt-templates/promptTemplates'
import styles from '../prompt-templates/PromptTemplates.module.css'

interface PromptBuildSectionProps {
  /** `GET /api/runs/{id}` の `prompt_build`(テンプレートで作った Run だけ。それ以外は null)。 */
  promptBuild: unknown
  headingClassName?: string
}

export function PromptBuildSection({ promptBuild, headingClassName }: PromptBuildSectionProps) {
  const { t } = useI18n()
  const r = t.promptTemplates.runDetail
  const info = parsePromptBuild(promptBuild)
  if (info === null) return null

  function modeLabel(mode: PromptBuildSlot['mode']): string {
    switch (mode) {
      case 'fixed':
        return r.modeFixed
      case 'off':
        return r.modeOff
      case 'text':
        return r.modeText
      case 'combo':
        return r.modeCombo
      default:
        return r.modeRandom
    }
  }

  return (
    <>
      <h2 className={headingClassName}>{r.heading}</h2>
      <p className={styles.buildMeta}>{fmt(r.templateLine, { name: info.templateName })}</p>
      {info.combos.length > 0 && (
        <p className={styles.buildMeta}>
          {fmt(r.combosLine, { names: info.combos.map((combo) => combo.name).join(', ') })}
        </p>
      )}
      <ul className={styles.buildList}>
        {info.slots.map((slot, index) => (
          <li key={`${slot.slotId}-${index}`} className={styles.buildItem}>
            <span className={styles.buildSlotName}>{slot.name}</span>
            <span className={styles.buildMode}>{modeLabel(slot.mode)}</span>
            <span className={styles.buildText}>
              {slot.variantName !== null && slot.variantName !== '' ? `${slot.variantName}: ` : ''}
              {slot.text.trim() === '' ? r.emptyText : slot.text}
            </span>
          </li>
        ))}
      </ul>
    </>
  )
}

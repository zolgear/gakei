/**
 * プロンプト欄の「テキスト / タグ」の切り替え(ADR-0039 2章)。2つのボタンの組で、押されている
 * ほうを `aria-pressed` で示す。どちらのモードも同じ文字列を編集する。
 */
import { fmt, useI18n } from '../../i18n'
import type { PromptEditMode } from './promptEditModePrefs'
import styles from './PromptTagEditor.module.css'

interface PromptEditModeToggleProps {
  mode: PromptEditMode
  onChange: (mode: PromptEditMode) => void
  /** 欄の名前(「プロンプト」「ネガティブプロンプト」)。グループの読み上げに使う。 */
  fieldLabel: string
  disabled?: boolean
}

export function PromptEditModeToggle({ mode, onChange, fieldLabel, disabled = false }: PromptEditModeToggleProps) {
  const { t } = useI18n()
  const pt = t.promptTags
  const options: { value: PromptEditMode; label: string; title: string }[] = [
    { value: 'text', label: pt.modeText, title: pt.modeTextTitle },
    { value: 'tags', label: pt.modeTags, title: pt.modeTagsTitle },
  ]
  return (
    <span className={styles.modeToggle} role="group" aria-label={fmt(pt.modeGroupLabel, { field: fieldLabel })}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={styles.modeButton}
          aria-pressed={mode === option.value}
          title={option.title}
          disabled={disabled}
          onClick={() => {
            if (mode !== option.value) onChange(option.value)
          }}
        >
          {option.label}
        </button>
      ))}
    </span>
  )
}

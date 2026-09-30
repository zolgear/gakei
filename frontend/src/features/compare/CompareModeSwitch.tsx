/**
 * 比較の表示モード(並べて表示 / スライダー)の切り替え。比較ビュー(`CompareView`)と共有のページ
 * (ADR-0029)で共用する。狭い幅ではスライダーに固定するので、呼び出し側が出し分ける。
 */
import { useI18n } from '../../i18n'
import type { CompareMode } from './compareState'
import styles from './CompareView.module.css'

export function CompareModeSwitch({ mode, onChange }: { mode: CompareMode; onChange: (mode: CompareMode) => void }) {
  const { t } = useI18n()
  return (
    <div className={styles.modeSwitch} role="group" aria-label={t.compare.modeGroupLabel}>
      <button
        type="button"
        className={styles.modeButton}
        data-active={mode === 'side'}
        aria-label={t.compare.sideBySide}
        title={t.compare.sideBySideTitle}
        onClick={() => onChange('side')}
      >
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="1.5" y="2.5" width="5.5" height="11" rx="1" stroke="currentColor" strokeWidth="1.3" />
          <rect x="9" y="2.5" width="5.5" height="11" rx="1" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      </button>
      <button
        type="button"
        className={styles.modeButton}
        data-active={mode === 'slider'}
        aria-label={t.compare.slider}
        title={t.compare.sliderTitle}
        onClick={() => onChange('slider')}
      >
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="1.5" y="2.5" width="13" height="11" rx="1" stroke="currentColor" strokeWidth="1.3" />
          <path d="M8 2.5v11" stroke="currentColor" strokeWidth="1.3" />
        </svg>
      </button>
    </div>
  )
}

/**
 * `ParamDef.widget === 'seed'` のときに `ParamField` の代わりに出す専用の入力欄
 * (ADR-0013 フォローアップ)。「ランダム」(params に seed を含めずサーバーに決めさせる)と
 * 「固定」(0〜`maximum`の数値)を切り替え、固定のときは乱数を振り直すボタンを添える。
 * 既定モードは「固定」だが、実際の初期モードはブラウザに記憶した前回の選択
 * (`seedModePrefs.ts`。ワークフローに依存しない)を使う(埋め込みは `seedDefaults.ts` が行う)。
 * ラジオボタンでモードを切り替えるとその選択を記憶するが、乱数ボタンや数値欄への直接入力では
 * モードを変えない。値の文字列表現は他の int パラメータと同じ(空文字列 = 未指定)なので、
 * `buildParams` はそのまま使える。
 */
import type { ParamDef } from '../../api/client'
import { useI18n } from '../../i18n'
import { isSeedRandom, randomSeedValue, seedMaximum } from './seedRandom'
import { saveSeedMode } from './seedModePrefs'
import fieldStyles from './ParamField.module.css'
import styles from './SeedField.module.css'

interface SeedFieldProps {
  def: ParamDef
  value: string
  enabled: boolean
  onChange: (name: string, value: string) => void
}

function DiceIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="2" y="2" width="12" height="12" rx="2.5" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="5.2" cy="5.2" r="1" fill="currentColor" />
      <circle cx="10.8" cy="5.2" r="1" fill="currentColor" />
      <circle cx="8" cy="8" r="1" fill="currentColor" />
      <circle cx="5.2" cy="10.8" r="1" fill="currentColor" />
      <circle cx="10.8" cy="10.8" r="1" fill="currentColor" />
    </svg>
  )
}

export function SeedField({ def, value, enabled, onChange }: SeedFieldProps) {
  const { t } = useI18n()
  const sf = t.runForm.seedField
  const id = `param-${def.name}`
  const random = isSeedRandom(value)

  function handleModeChange(nextRandom: boolean) {
    saveSeedMode(nextRandom ? 'random' : 'fixed')
    onChange(def.name, nextRandom ? '' : String(randomSeedValue(undefined, seedMaximum(def))))
  }

  return (
    <div className={fieldStyles.field} data-disabled={!enabled}>
      <label htmlFor={id} title={def.name}>
        {def.label}
      </label>
      <div className={styles.row}>
        <label className={styles.modeLabel}>
          <input
            type="radio"
            name={`${id}-mode`}
            checked={random}
            disabled={!enabled}
            onChange={() => handleModeChange(true)}
          />
          {sf.random}
        </label>
        <label className={styles.modeLabel}>
          <input
            type="radio"
            name={`${id}-mode`}
            checked={!random}
            disabled={!enabled}
            onChange={() => handleModeChange(false)}
          />
          {sf.fixed}
        </label>
        {!random && (
          <div className={styles.fixedGroup}>
            <input
              id={id}
              className={styles.numberInput}
              type="number"
              min={0}
              max={def.maximum ?? undefined}
              value={value}
              disabled={!enabled}
              onChange={(e) => onChange(def.name, e.target.value)}
            />
            <button
              type="button"
              className={styles.diceButton}
              title={sf.reroll}
              aria-label={sf.reroll}
              disabled={!enabled}
              onClick={() => onChange(def.name, String(randomSeedValue(undefined, seedMaximum(def))))}
            >
              <DiceIcon />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

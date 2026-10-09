/**
 * サイズ入力。プリセット選択 + 任意の幅×高さ。
 * 「◯の倍数に丸める」ボタンは廃止し、代わりに入力欄の blur 時と送信時に自動で丸める
 * (RunForm.tsx の送信処理側。ここでは blur 時の丸めと、丸めたことを知らせる一時的な注記を持つ)。
 */
import { useEffect, useRef, useState } from 'react'
import type { SizeConstraints } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import {
  customSizeLabel,
  hasExperimentalSizes,
  isExperimentalSize,
  roundSizeStateToMultiple,
  roundToMultiple,
  sizePresetsFor,
  validateSizeState,
  type SizeState,
} from './sizeValidation'
import styles from './ParamField.module.css'
import sizeStyles from './SizeInput.module.css'

interface SizeInputProps {
  constraints: SizeConstraints
  value: SizeState
  onChange: (next: SizeState) => void
}

interface RoundNote {
  field: 'width' | 'height'
  from: number
  to: number
}

// 一時的な注記を消すまでの時間(ミリ秒)。
const ROUND_NOTE_TIMEOUT_MS = 4000

function findPresetIndex(presets: ReturnType<typeof sizePresetsFor>, value: SizeState): number {
  return presets.findIndex((p) => {
    if (p.value.mode !== value.mode) return false
    if (p.value.mode !== 'custom') return true
    return p.value.width === value.width && p.value.height === value.height
  })
}

export function SizeInput({ constraints, value, onChange }: SizeInputProps) {
  const { t } = useI18n()
  const si = t.runForm.sizeInput
  // プロバイダーの制約で選べるものだけ(auto を受け付けない・長辺が短いプロバイダーなど)。
  const presets = sizePresetsFor(constraints)
  const presetIndex = findPresetIndex(presets, value)
  const selectValue = presetIndex >= 0 ? String(presetIndex) : 'custom'

  // 表示中の値に対して丸めた後の状態で検証する(丸めは blur・送信時に自動で行われるため、
  // 「16の倍数でない」エラーは実質常に解消される想定。それでも残る制約だけをここで示す)。
  const effectiveState = roundSizeStateToMultiple(constraints, value)
  const validation = validateSizeState(constraints, effectiveState)
  const experimental =
    value.mode === 'custom' &&
    hasExperimentalSizes(constraints) &&
    isExperimentalSize(effectiveState.width, effectiveState.height)

  const [roundNote, setRoundNote] = useState<RoundNote | null>(null)
  const noteTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (noteTimeoutRef.current) clearTimeout(noteTimeoutRef.current)
    }
  }, [])

  function showRoundNote(note: RoundNote) {
    setRoundNote(note)
    if (noteTimeoutRef.current) clearTimeout(noteTimeoutRef.current)
    noteTimeoutRef.current = setTimeout(() => setRoundNote(null), ROUND_NOTE_TIMEOUT_MS)
  }

  function handlePresetChange(raw: string) {
    if (raw === 'custom') {
      onChange({ mode: 'custom', width: value.width || 1024, height: value.height || 1024 })
      return
    }
    const preset = presets[Number(raw)].value
    onChange({ mode: preset.mode, width: preset.width ?? 1024, height: preset.height ?? 1024 })
  }

  function handleWidthBlur() {
    const rounded = roundToMultiple(value.width, constraints.multiple_of)
    if (rounded !== value.width) {
      showRoundNote({ field: 'width', from: value.width, to: rounded })
      onChange({ ...value, width: rounded })
    }
  }

  function handleHeightBlur() {
    const rounded = roundToMultiple(value.height, constraints.multiple_of)
    if (rounded !== value.height) {
      showRoundNote({ field: 'height', from: value.height, to: rounded })
      onChange({ ...value, height: rounded })
    }
  }

  return (
    <div className={sizeStyles.sizeField}>
      <label htmlFor="size-preset" title="size">
        {si.label}
      </label>
      <div className={sizeStyles.sizeRow}>
        <select
          id="size-preset"
          className={sizeStyles.presetSelect}
          value={selectValue}
          onChange={(e) => handlePresetChange(e.target.value)}
        >
          {presets.map((preset, index) => (
            <option key={preset.label} value={index}>
              {preset.label}
            </option>
          ))}
          <option value="custom">{customSizeLabel()}</option>
        </select>

        {value.mode === 'custom' && (
          <>
            <input
              id="size-width"
              type="number"
              inputMode="numeric"
              aria-label={si.widthAria}
              className={sizeStyles.dimensionInput}
              value={value.width}
              onChange={(e) => onChange({ ...value, width: Number(e.target.value) })}
              onBlur={handleWidthBlur}
            />
            <span className={sizeStyles.times} aria-hidden="true">
              ×
            </span>
            <input
              id="size-height"
              type="number"
              inputMode="numeric"
              aria-label={si.heightAria}
              className={sizeStyles.dimensionInput}
              value={value.height}
              onChange={(e) => onChange({ ...value, height: Number(e.target.value) })}
              onBlur={handleHeightBlur}
            />
          </>
        )}
      </div>

      {roundNote && (
        <p className={styles.hint}>
          {fmt(si.roundedNote, {
            multipleOf: constraints.multiple_of,
            field: roundNote.field === 'width' ? si.fieldWidth : si.fieldHeight,
            from: roundNote.from,
            to: roundNote.to,
          })}
        </p>
      )}

      {value.mode === 'custom' && experimental && (
        <p className={styles.hint}>{si.experimentalNote}</p>
      )}

      {!validation.valid && (
        <ul className={sizeStyles.errors}>
          {validation.errors.map((err) => (
            <li key={err}>{err}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

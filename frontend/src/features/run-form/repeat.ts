/**
 * 繰り返し回数(ADR-0042)。生成ボタンを1回押すと、同じ設定の Run をこの数だけまとめて積む。
 *
 * 繰り返し回数は Run の記録(`run.params`)に入らない(API に送った値ではないため。ADR-0003
 * ルール4)。そのためパラメーターセットや「同じ設定で開く」には入れず、ブラウザのフォームの
 * 状態としてだけ localStorage に覚える(`seedModePrefs.ts` と同じく try/catch で囲む)。
 */
import type { ParamDef } from '../../api/client'
import { fmt, msg } from '../../i18n'

/** サーバーと同じ範囲(`POST /api/runs` の `repeat`)。 */
export const REPEAT_MIN = 1
export const REPEAT_MAX = 20

const STORAGE_KEY = 'gakei.runForm.repeat'

/** 欄の生の文字列を回数にする。空欄は既定の 1。範囲外・整数でなければ null(送れない)。 */
export function parseRepeat(raw: string): number | null {
  const trimmed = raw.trim()
  if (trimmed === '') return REPEAT_MIN
  if (!/^\d+$/.test(trimmed)) return null
  const value = Number(trimmed)
  if (value < REPEAT_MIN || value > REPEAT_MAX) return null
  return value
}

/** 覚えている欄の値を読む。無い・壊れている・範囲外なら空欄(= 1)。 */
export function loadRepeatRaw(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === null) return ''
    return parseRepeat(stored) === null ? '' : stored
  } catch {
    return ''
  }
}

/** 欄の値を覚える。失敗しても(容量超過・プライベートブラウジング等)黙って無視する。 */
export function saveRepeatRaw(raw: string): void {
  try {
    if (raw === '' || raw === String(REPEAT_MIN)) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, raw)
  } catch {
    // 保存できなくても致命的ではない。
  }
}

/** 繰り返し回数の欄の定義。パラメーターの欄(`ParamField`)と同じ見た目で出すために作る。 */
export function repeatParamDef(): ParamDef {
  const t = msg().runForm.repeat
  return {
    name: REPEAT_FIELD_NAME,
    type: 'int',
    label: t.label,
    minimum: REPEAT_MIN,
    maximum: REPEAT_MAX,
    default: REPEAT_MIN,
    required: false,
    description: fmt(t.description, { max: REPEAT_MAX }),
  }
}

/** 繰り返し回数の欄の名前(パラメーターの名前と重ならない、送らない名前)。 */
export const REPEAT_FIELD_NAME = 'gakei_repeat'

/** 生成ボタンの文言。2 回以上なら「生成 ×5」のように回数を添える。 */
export function submitButtonLabel(repeat: number | null): string {
  const t = msg().workspace.inputPane
  if (repeat === null || repeat < 2) return t.generate
  return fmt(t.generateRepeat, { count: repeat })
}

/**
 * 繰り返しを含めた合計の枚数。`perRun` は1回(1つの Run)の枚数。2 回以上のときだけ文言を返す
 * (1 回なら既存の表示のまま)。
 */
export function repeatTotalText(perRun: number, repeat: number | null): string | null {
  if (repeat === null || repeat < 2) return null
  return fmt(msg().runForm.repeat.total, { total: perRun * repeat, perRun, count: repeat })
}

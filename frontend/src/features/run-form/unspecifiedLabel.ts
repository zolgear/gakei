/**
 * 値を指定しないときに入力欄へ出す文言。ParamDef.default(指定しなかったときに使われる値。
 * ComfyUI ではワークフローに書かれた値)があれば「既定値 (X)」、無ければ「未指定」。
 */
import { fmt, msg } from '../../i18n'
import type { ParamDef } from '../../api/client'

function formatDefault(def: ParamDef): string | null {
  const value = def.default
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'number') {
    // 0.7000000000000001 のような浮動小数点の誤差を丸める。float の整数値は 1.0 と書く。
    const rounded = Number(value.toPrecision(10))
    if (def.type === 'float' && Number.isInteger(rounded)) return rounded.toFixed(1)
    return String(rounded)
  }
  if (typeof value === 'string') return def.choice_labels?.[value] ?? value
  if (typeof value === 'boolean') {
    const pf = msg().runForm.paramField
    return value ? pf.boolOn : pf.boolOff
  }
  return null
}

/** 数値・テキスト入力の placeholder。 */
export function unspecifiedPlaceholder(def: ParamDef): string {
  const t = msg().runForm.unspecifiedLabel
  const formatted = formatDefault(def)
  return formatted === null ? t.unspecified : fmt(t.defaultValue, { formatted })
}

/** select の「指定しない」選択肢の文言。 */
export function unspecifiedOptionLabel(def: ParamDef): string {
  const t = msg().runForm.unspecifiedLabel
  const formatted = formatDefault(def)
  return formatted === null ? t.unspecifiedAuto : fmt(t.defaultValue, { formatted })
}

/**
 * bool の項目のスイッチに出す状態(純粋関数)。未指定のあいだは、指定しなかったときに使われる値
 * (`default`、無ければ `form_default`、それも無ければオフ)を出す。送る値の意味は変えない
 * (未指定なら送らず、サーバーの既定になる)。
 */
import type { ParamDef } from '../../api/client'

function asBool(value: unknown): boolean | null {
  if (value === true || value === 'true') return true
  if (value === false || value === 'false') return false
  return null
}

export function boolDisplayValue(def: Pick<ParamDef, 'default' | 'form_default'>, raw: string): boolean {
  return asBool(raw) ?? asBool(def.default) ?? asBool(def.form_default) ?? false
}

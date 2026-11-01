/**
 * 選択肢の少ない enum を並べて出すスイッチ(`widget: 'segmented'`)の、押された状態に出す選択肢
 * (純粋関数)。未指定のあいだは、指定しなかったときに使われる値(`default`、無ければ
 * `form_default`)を出す。送る値の意味は変えない(未指定なら送らず、サーバーの既定になる)。
 */
import type { ParamDef } from '../../api/client'

export function segmentedDisplayValue(
  def: Pick<ParamDef, 'choices' | 'default' | 'form_default'>,
  raw: string,
): string | null {
  const choices = def.choices ?? []
  for (const candidate of [raw, def.default, def.form_default]) {
    if (candidate === null || candidate === undefined) continue
    const value = String(candidate)
    if (choices.includes(value)) return value
  }
  return null
}

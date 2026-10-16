/**
 * 送り先の区分(`locality`。ADR-0051 3章)の表示の純粋関数。
 * GAKEI は自動で判定せず、この値で動作も変えない。`ai.before_send` に載せて、判断は Mod に任せる。
 * 区分の欄は、Mod を1つも有効にしていなくても出す。
 */
import type { Locality } from '../../api/client'
import { fmt, msg } from '../../i18n'

export const LOCALITY_VALUES: readonly Locality[] = ['local', 'cloud', 'unset']

export function isLocality(value: unknown): value is Locality {
  return typeof value === 'string' && (LOCALITY_VALUES as readonly string[]).includes(value)
}

/**
 * 選択肢・表示の名前。`inherit` はワークフローの欄(`unset` を「接続の設定に従う」と出す)。
 * `defaultValue` を渡すと、その値に「(既定)」を添える。
 */
export function localityLabel(
  value: Locality,
  options: { inherit?: boolean; defaultValue?: Locality } = {},
): string {
  const l = msg().settings.locality
  const base = value === 'unset' ? (options.inherit ? l.inherit : l.unset) : l[value]
  return options.defaultValue === value ? fmt(l.defaultSuffix, { label: base }) : base
}

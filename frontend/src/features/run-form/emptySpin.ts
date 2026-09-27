/**
 * 数値パラメーターの入力欄が空のときに、スピンボタン(上下の矢印)や
 * キーボードの ArrowUp/ArrowDown を使った場合の値を計算する。
 * 空欄でのスピンはブラウザが 0 を起点にしてしまうため、代わりに
 * ParamDef.default から step ぶんずらした値を返す。
 * default が数値でなければ null を返し、呼び出し側はブラウザの既定の挙動に任せる。
 */
import type { ParamDef } from '../../api/client'

export type SpinDirection = 'up' | 'down'

/**
 * 空欄のときは min / max を外して step を 1 にしておく(`emptySpinProbeAttrs`)。
 * こうするとブラウザが空欄から入れる値は必ず 1(上)か -1(下)になるので、
 * どちらの矢印が押されたかを結果の値から確実に判定できる。
 * スピンボタンの上で pointerdown が拾えないブラウザ(Chrome)があるため、
 * クリック位置ではなくこの方法で向きを決める。
 */
export const EMPTY_SPIN_PROBE_STEP = 1

/** 空欄からの step 操作の結果値から、押された向きを判定する。判定できなければ null。 */
export function directionFromProbeValue(raw: string): SpinDirection | null {
  if (raw === String(EMPTY_SPIN_PROBE_STEP)) return 'up'
  if (raw === String(-EMPTY_SPIN_PROBE_STEP)) return 'down'
  return null
}

export function spinFromDefault(def: ParamDef, direction: SpinDirection): string | null {
  const base = def.default
  if (typeof base !== 'number' || !Number.isFinite(base)) return null

  const rawStep = def.step
  const step = typeof rawStep === 'number' && Number.isFinite(rawStep) && rawStep > 0 ? rawStep : 1

  let value = direction === 'up' ? base + step : base - step

  if (typeof def.minimum === 'number' && value < def.minimum) value = def.minimum
  if (typeof def.maximum === 'number' && value > def.maximum) value = def.maximum

  // 浮動小数点の誤差を丸める(0.7 + 0.1 = 0.7999999999999999 のような結果を避ける)。
  value = Number(value.toPrecision(10))

  if (def.type === 'int') value = Math.round(value)

  return String(value)
}

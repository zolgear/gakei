/**
 * サムネイルの焦点(ADR-0043)。`object-fit: cover` で枠に切り取って出すサムネイルに
 * `object-position` を付け、顔の位置が枠に入るように見せる。焦点が無ければ今までどおり中央。
 * ビューアの全体表示(contain)には使わない。
 */
import type { CSSProperties } from 'react'

/** API の `focal_point`(画像の幅・高さに対する 0〜1 の位置)。 */
export type FocalPointValue = { x: number; y: number }

function toPercent(value: number): string {
  const clamped = Math.min(1, Math.max(0, value))
  // 小数第1位まで(0.3456 → "34.6%")。
  return `${Math.round(clamped * 1000) / 10}%`
}

/** 焦点から `object-position` の値を作る(例: `"35% 20%"`)。無い・壊れていれば undefined。 */
export function focalObjectPosition(fp: FocalPointValue | null | undefined): string | undefined {
  if (!fp || !Number.isFinite(fp.x) || !Number.isFinite(fp.y)) return undefined
  return `${toPercent(fp.x)} ${toPercent(fp.y)}`
}

/** `<img style>` にそのまま渡せる形。焦点が無ければ undefined(style を付けない)。 */
export function focalStyle(fp: FocalPointValue | null | undefined): CSSProperties | undefined {
  const position = focalObjectPosition(fp)
  return position ? { objectPosition: position } : undefined
}

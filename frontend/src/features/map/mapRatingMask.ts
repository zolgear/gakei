/**
 * マップのマスク(ADR-0049 5章「マップ」。2026-10-15)。見た目を持たない計算と、マップ全体の
 * 切り替えの状態だけをここに置く。
 *
 * - どのノードをマスクするかは、ほかの画面と同じユーザー設定(`useContentRatingMask().shouldMask`)で
 *   決める。1枚ずつは外さず、ツールバーの目のボタンでマップ全体を切り替える。
 * - 切り替えはメモリにだけ置き(ページを読み直すとマスクに戻る)、1枚ずつの「一時的に見る」
 *   (`revealStore`)とは別に持つ(マップで外しても履歴などの状態は変えない)。
 */
import { useSyncExternalStore } from 'react'
import type { ContentRatingLevel } from '../../api/client'

type ShouldMask = (level: ContentRatingLevel | null | undefined) => boolean
type RatedNode = { content_rating?: ContentRatingLevel | null }

/** これ以上の大きさ(CSS ピクセル)で描くタイルには、段階のバッジを重ねる。 */
export const RATING_LABEL_MIN_PX = 40

/**
 * ぼかしたタイルを作るときに、いったん縮める一辺(ピクセル)。小さいほど強くぼける。
 * `ctx.filter`(Safari が対応しない)を使わず、縮めて引き伸ばすことでぼかす。
 */
export const BLUR_SAMPLE_PX = 8

/**
 * ノードごとにマスクするか(1 ならマスク)。`active` が false(マップ全体で外している)か、
 * マスクするノードが1つも無ければ null。
 */
export function mapMaskFlags(nodes: readonly RatedNode[], shouldMask: ShouldMask, active: boolean): Uint8Array | null {
  if (!active) return null
  let flags: Uint8Array | null = null
  for (let i = 0; i < nodes.length; i++) {
    if (!shouldMask(nodes[i].content_rating)) continue
    flags ??= new Uint8Array(nodes.length)
    flags[i] = 1
  }
  return flags
}

/** マップにマスクする画像があるか(ツールバーの目のボタンを出すか)。 */
export function mapHasMaskable(nodes: readonly RatedNode[], shouldMask: ShouldMask): boolean {
  return nodes.some((node) => shouldMask(node.content_rating))
}

/**
 * ツールバーの目のボタン。マスクする画像がマップに無ければ出さない(外しているあいだも、
 * マスクに戻せるよう出したままにする)。`pressed` はマップ全体で外しているか。
 */
export function mapRatingToggle(
  nodes: readonly RatedNode[],
  shouldMask: ShouldMask,
  unmasked: boolean,
): { visible: boolean; pressed: boolean } {
  const visible = mapHasMaskable(nodes, shouldMask)
  return { visible, pressed: visible && unmasked }
}

/**
 * ぼかしたタイルに重ねる段階の文字。小さく描くタイルには出さない(null)。未判定(「未判定も
 * マスクする」のとき)は `AssetThumb` と同じく「未判定」と出す。
 */
export function ratingTileLabel(
  level: ContentRatingLevel | null | undefined,
  tilePx: number,
  texts: { shortLevels: Record<ContentRatingLevel, string>; unrated: string },
): { text: string; level: ContentRatingLevel | 'unrated' } | null {
  if (tilePx < RATING_LABEL_MIN_PX) return null
  if (!level) return { text: texts.unrated, level: 'unrated' }
  return { text: texts.shortLevels[level], level }
}

/**
 * canvas に描く段階のバッジの色。`content-rating/ContentRating.module.css` の `.badge[data-level]` と
 * 同じ値(canvas は CSS を読めないので写す。変えるときは両方を合わせる)。未判定とグリーンの縁と
 * 文字は配色(CSS 変数)から読むので、ここでは null にする。
 */
export const RATING_CANVAS_COLORS: Record<
  ContentRatingLevel | 'unrated',
  { bg: string; border: string | null; text: string | null }
> = {
  g: { bg: 'rgba(23, 24, 27, 0.75)', border: null, text: null },
  r: { bg: '#8a6a1c', border: '#c99a2e', text: '#fff6dc' },
  x: { bg: '#9a4f1d', border: '#d9773a', text: '#fff0e4' },
  xx: { bg: '#9e2f2a', border: '#e0594f', text: '#ffeceb' },
  xxx: { bg: '#7d1f55', border: '#c9418c', text: '#ffe8f4' },
  unrated: { bg: 'rgba(23, 24, 27, 0.85)', border: null, text: null },
}

// ---- マップ全体の切り替え(メモリにだけ置く) ----

let unmasked = false
const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** マップ全体でマスクを外しているか。 */
export function isMapUnmasked(): boolean {
  return unmasked
}

export function setMapUnmasked(value: boolean): void {
  if (unmasked === value) return
  unmasked = value
  for (const listener of listeners) listener()
}

export function useMapUnmasked(): boolean {
  return useSyncExternalStore(subscribe, isMapUnmasked, isMapUnmasked)
}

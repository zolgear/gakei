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

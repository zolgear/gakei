/**
 * `@` メンション候補ポップオーバーの表示位置を決める純粋関数。DOM には触れず、caret の
 * 画面座標(`caretCoordinates.ts` が計測する)と可視領域の矩形から、上下どちらに開くか・
 * 最大高さ・左右位置を計算する。
 *
 * 「上に開く」場合、実際の中身の高さ(候補件数で変わる)はここでは扱わない。呼び出し側は
 * `top` を「その行の上端からの距離」として使い、CSS の `transform: translateY(-100%)` で
 * 要素自身の実高さぶん上へずらす(ポップオーバーの下端を `top` に固定する)。これにより
 * 中身の高さを事前計算せずに、常にカーソル行のすぐ上に正確に貼り付く。
 */

export interface ViewportRect {
  top: number
  left: number
  width: number
  height: number
}

export interface MentionPlacementInput {
  /** caret がある行の上端(viewport 基準、`position: fixed` と同じ座標系)。 */
  caretTop: number
  /** caret がある行の下端。 */
  caretBottom: number
  /** caret の左端。 */
  caretLeft: number
  /** ポップオーバーの希望幅(textarea 幅などから呼び出し側が決める)。 */
  contentWidth: number
  /** 可視領域(`window.visualViewport` があればそれ、無ければ `window` 全体)。 */
  viewport: ViewportRect
  /** 上端としてこれより上には出さない Y 座標(App バー下端など)。省略時は `viewport.top`。 */
  minTop?: number
}

export interface MentionPlacement {
  /**
   * 上向き(`direction === 'up'`)のときは caret 行の上端からの距離(要素は
   * `translateY(-100%)` と組み合わせて使う想定)。下向きのときはそのまま要素の `top`。
   */
  top: number
  left: number
  width: number
  maxHeight: number
  direction: 'up' | 'down'
}

/** caret 行とポップオーバーの間の余白。 */
export const MENTION_GAP = 6
/** 画面端からの最小余白。 */
export const MENTION_VIEWPORT_MARGIN = 16
/** 上方向の空きがこれ未満なら、行の下に開く。 */
export const MENTION_MIN_UP_SPACE = 160
/** ポップオーバーの幅の上限。 */
export const MENTION_POPOVER_MAX_WIDTH = 480

export function computeMentionPlacement(input: MentionPlacementInput): MentionPlacement {
  const { viewport } = input
  const minTop = input.minTop ?? viewport.top
  const viewportBottom = viewport.top + viewport.height
  const viewportRight = viewport.left + viewport.width

  const spaceAbove = Math.max(0, input.caretTop - MENTION_GAP - minTop)
  const spaceBelow = Math.max(0, viewportBottom - (input.caretBottom + MENTION_GAP))

  const direction: MentionPlacement['direction'] = spaceAbove >= MENTION_MIN_UP_SPACE ? 'up' : 'down'
  const maxHeight = direction === 'up' ? spaceAbove : spaceBelow
  const top = direction === 'up' ? input.caretTop - MENTION_GAP : input.caretBottom + MENTION_GAP

  const width = Math.max(0, Math.min(input.contentWidth, viewport.width - MENTION_VIEWPORT_MARGIN * 2))
  let left = input.caretLeft
  left = Math.min(left, viewportRight - MENTION_VIEWPORT_MARGIN - width)
  left = Math.max(left, viewport.left + MENTION_VIEWPORT_MARGIN)

  return { top, left, width, maxHeight, direction }
}

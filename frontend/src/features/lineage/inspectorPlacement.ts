/**
 * 系列インスペクターをどう配置するかを、上段(結果エリア)の実高さと画面幅から決める純粋関数。
 * - モバイル幅(<768px)は常にボトムシート。
 * - 上段の実高さが 360px 未満なら、上段の枠に縛られずメイン領域の右側(下段まで覆う)に出す。
 * - それ以外はグラフの右にオーバーレイ(上段の高さいっぱい)。
 */
export type InspectorPlacement = 'right-overlay' | 'main-right' | 'sheet'

const MOBILE_BREAKPOINT_PX = 768
const MIN_OVERLAY_HEIGHT_PX = 360

export function resolveInspectorPlacement(paneHeightPx: number, viewportWidthPx: number): InspectorPlacement {
  if (viewportWidthPx < MOBILE_BREAKPOINT_PX) return 'sheet'
  if (paneHeightPx < MIN_OVERLAY_HEIGHT_PX) return 'main-right'
  return 'right-overlay'
}

const MAX_INSPECTOR_WIDTH_PX = 440

/**
 * インスペクターの実際の幅(px)を、配置(CSS の `width: min(440px, 50%)`)と同じ計算で求める。
 * `right-overlay` は上段(グラフの入れ物)の幅の50%が基準、`main-right` は `position: fixed`
 * なので画面幅の50%が基準(CSSのパーセント基準と揃える必要がある)。`sheet` はグラフに重ならない
 * ため常に0。
 */
export function computeInspectorWidthPx(
  placement: InspectorPlacement,
  containerWidthPx: number,
  viewportWidthPx: number,
): number {
  if (placement === 'sheet') return 0
  const base = placement === 'main-right' ? viewportWidthPx : containerWidthPx
  return Math.min(MAX_INSPECTOR_WIDTH_PX, base / 2)
}

/**
 * インスペクターの幅が `previousWidthPx` → `nextWidthPx` に変わったとき、React Flow の
 * ビューポート x に足すべき補正量。パネルが開いた/広がった分だけグラフを左へ
 * (x を減らす)、閉じた/狭まった分だけ右へ(x を増やす)動かす。
 */
export function computeViewportXAdjustment(previousWidthPx: number, nextWidthPx: number): number {
  // (next - previous) の符号を反転させる代わりに引き算の順序を入れ替える: 変化が無いとき
  // `-0` ではなく `0` になるようにするため(`Object.is` で区別されるテスト・比較を素直にする)。
  return (previousWidthPx - nextWidthPx) / 2
}

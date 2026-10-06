/**
 * 系列グラフの表示倍率の決め方(通常の画面の系列グラフ・サイドバーの小さい表示・共有のページで共通)。
 *
 * 子孫も祖先と同じく実質無制限に辿るようになり(ADR-0014 6章、2026-10-07 追記)、ノードが
 * 多い系列では「全体が収まる倍率」がとても小さくなる。開いたとき(と枠の大きさが変わったとき)の
 * 自動の表示は、全体が読める倍率で収まるならそのまま全体を収め、収まらないときは読める倍率の
 * 下限で止めて、注目しているノード(開いている画像・選んだノード、無ければ起点)を中央に置く。
 * 「全体を表示」ボタンは、利用者が明示した操作なので下限を手動の最小倍率まで下げて全体を収める。
 */
import { getViewportForBounds, type Rect, type Viewport } from '@xyflow/react'

/**
 * 自動の表示で下げる倍率の下限。ノードの文字は 11px(`LineageNodes.module.css`)で、0.75 倍で
 * 約 8.3px。これより下げると短い ID やモデル名が読めなくなる。サムネイル(通常 128px 幅)は
 * 96px 幅になり、絵柄の見分けがつく。
 */
export const LINEAGE_READABLE_MIN_ZOOM = 0.75

/**
 * 手動のズームアウトと「全体を表示」の下限。React Flow の既定(0.5)のままだと、長い系列
 * (ノードの上限 1000 件)は全体を収められない。
 */
export const LINEAGE_MANUAL_MIN_ZOOM = 0.05

/** React Flow の `maxZoom` の既定と同じ。少ないノードを大きくしすぎない。 */
export const LINEAGE_DEFAULT_MAX_ZOOM = 2

/** React Flow の `fitView` の既定と同じ(枠に対する割合)。 */
export const LINEAGE_DEFAULT_FIT_PADDING = 0.1

export interface LineageFitOptions {
  maxZoom?: number
  padding?: number
}

export interface LineageViewportInput {
  /** 全ノードの外接矩形(グラフ座標)。 */
  graphBounds: Rect
  /** キャンバスの幅と高さ(px)。 */
  width: number
  height: number
  /**
   * 右側に重なるパネル(インスペクター)の幅(px)。その分を除いた左側の領域に収める
   * (`inspectorPlacement.ts`)。
   */
  rightInsetPx?: number
  options?: LineageFitOptions
}

function visibleWidth(width: number, rightInsetPx: number | undefined): number {
  return Math.max(1, width - (rightInsetPx ?? 0))
}

/** 「全体を表示」の倍率と位置。手動の最小倍率まで下げてでも全体を収める。 */
export function computeFullFitViewport(input: LineageViewportInput): Viewport {
  const { graphBounds, width, height, rightInsetPx, options } = input
  return getViewportForBounds(
    graphBounds,
    visibleWidth(width, rightInsetPx),
    height,
    LINEAGE_MANUAL_MIN_ZOOM,
    options?.maxZoom ?? LINEAGE_DEFAULT_MAX_ZOOM,
    options?.padding ?? LINEAGE_DEFAULT_FIT_PADDING,
  )
}

/**
 * 開いたとき・枠の大きさが変わったときの倍率と位置。全体が読める倍率で収まれば全体を収め、
 * 収まらなければ `LINEAGE_READABLE_MIN_ZOOM` で止めて `focusBounds` の中心を領域の中央に置く。
 * `focusBounds` が無ければ(注目するノードが見つからない)、グラフの中心に置く。
 */
export function computeAutoFitViewport(
  input: LineageViewportInput & { focusBounds?: Rect | null },
): Viewport {
  const fitted = computeFullFitViewport(input)
  if (fitted.zoom >= LINEAGE_READABLE_MIN_ZOOM) return fitted

  const zoom = LINEAGE_READABLE_MIN_ZOOM
  const target = input.focusBounds ?? input.graphBounds
  const centerX = target.x + target.width / 2
  const centerY = target.y + target.height / 2
  const areaWidth = visibleWidth(input.width, input.rightInsetPx)
  return {
    x: areaWidth / 2 - centerX * zoom,
    y: input.height / 2 - centerY * zoom,
    zoom,
  }
}

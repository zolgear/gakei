/**
 * 履歴の「画像」の形(石積みのギャラリー)の計算。React から切り離した純粋関数。
 *
 * - タイルは画像の元の縦横比のまま列の幅いっぱいに出す。縦横比は出力の width / height から求める
 *   (読み込みの前に高さが決まり、ずれない)。出力の無い Run は params の size("1536x1024")、
 *   それも無ければ 1:1。
 * - 並べ方は、短い列から順に積む(時系列の順が左上から読める。CSS の columns は縦に流れて順が崩れる)。
 *   列の幅はどの列も同じなので、高さは「列の幅を 1 としたときの 1/縦横比」で比べられる。
 */
import type { RunOutputRef, RunSummary } from '../../api/client'

/** 縦横比(幅 / 高さ)の範囲。極端な比率でタイルが潰れたり伸びすぎたりしないよう抑える。 */
const MIN_ASPECT = 0.25
const MAX_ASPECT = 4

function clampAspect(aspect: number): number {
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, aspect))
}

/** 出力のピクセルの大きさの表示("1536×1024")。分からなければ null。 */
export function formatPixelSize(output: Pick<RunOutputRef, 'width' | 'height'> | undefined): string | null {
  if (!output?.width || !output?.height) return null
  return `${output.width}×${output.height}`
}

/** params の size("1536x1024")から縦横比。読めなければ null("auto" など)。 */
export function aspectFromSizeParam(params: Record<string, unknown> | null | undefined): number | null {
  const size = params?.size
  if (typeof size !== 'string') return null
  const match = /^(\d+)\s*[x×]\s*(\d+)$/i.exec(size.trim())
  if (!match) return null
  const width = Number(match[1])
  const height = Number(match[2])
  if (!(width > 0) || !(height > 0)) return null
  return width / height
}

/** タイルの縦横比。出力の大きさ、無ければ params の size、それも無ければ 1。 */
export function galleryTileAspect(
  run: Pick<RunSummary, 'params'>,
  output: Pick<RunOutputRef, 'width' | 'height'> | undefined,
): number {
  if (output?.width && output?.height) return clampAspect(output.width / output.height)
  const fromParams = aspectFromSizeParam(run.params as Record<string, unknown> | undefined)
  return fromParams ? clampAspect(fromParams) : 1
}

export interface MasonryColumnOptions {
  /** 列の最小の幅(px)。 */
  minColumnWidth: number
  /** 列の間(px)。 */
  gap: number
  /** 狭い画面(768px 未満)では列の数を固定する。 */
  narrowColumns?: number
  narrow?: boolean
}

/** コンテナの幅から列の数。最低 1 列。 */
export function masonryColumnCount(containerWidth: number, options: MasonryColumnOptions): number {
  if (options.narrow && options.narrowColumns) return options.narrowColumns
  if (!(containerWidth > 0)) return 1
  return Math.max(1, Math.floor((containerWidth + options.gap) / (options.minColumnWidth + options.gap)))
}

/**
 * 各項目(縦横比の並び)を、短い列から順に積んだときの列ごとの添字の並び。高さが同じなら左の列。
 * `gapRatio` は列の間の大きさを列の幅で割った値(タイルの下の間も高さに足す)。
 */
export function assignMasonryColumns(aspects: readonly number[], columnCount: number, gapRatio = 0): number[][] {
  const count = Math.max(1, Math.floor(columnCount))
  const columns: number[][] = Array.from({ length: count }, () => [])
  const heights = new Array<number>(count).fill(0)
  aspects.forEach((aspect, index) => {
    let shortest = 0
    for (let c = 1; c < count; c++) {
      if (heights[c] < heights[shortest] - 1e-9) shortest = c
    }
    columns[shortest].push(index)
    heights[shortest] += 1 / (aspect > 0 ? aspect : 1) + gapRatio
  })
  return columns
}

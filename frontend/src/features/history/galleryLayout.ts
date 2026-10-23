/**
 * 履歴のギャラリー(石積みと行揃え)の計算。React から切り離した純粋関数。
 *
 * - タイルは画像の元の縦横比のまま出す(切り抜かない)。縦横比は出力の width / height から求める
 *   (読み込みの前に大きさが決まり、ずれない)。出力の無い Run は params の size("1536x1024")、
 *   それも無ければ 1:1。
 * - 石積み(masonry): 列の幅いっぱいに出し、短い列から順に積む(時系列の順が左上から読める。
 *   CSS の columns は縦に流れて順が崩れる)。列の幅はどの列も同じなので、高さは「列の幅を 1 と
 *   したときの 1/縦横比」で比べられる。
 * - 行揃え(justified rows): 行の高さを揃えて左から右へ詰める。横長の画像も縦長と同じ高さで
 *   大きく見せられる。
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

export interface JustifiedRowsOptions {
  /** 目標の行の高さ(px)。 */
  targetHeight: number
  /** タイルの間(px。行の中と行の間)。 */
  gap: number
  /** 目標の何倍まで行を高くしてよいか(画像の少ない行が巨大にならないように)。既定は 1.5。 */
  maxScale?: number
}

export interface JustifiedTile {
  /** 元の並びでの添字。 */
  index: number
  width: number
  height: number
}

export interface JustifiedRow {
  height: number
  tiles: JustifiedTile[]
}

function finishRow(indexes: number[], aspects: readonly number[], height: number, containerWidth: number, gap: number, fill: boolean): JustifiedRow {
  const tiles = indexes.map((index) => ({ index, width: aspects[index] * height, height }))
  if (fill && tiles.length > 0) {
    // 丸めの誤差で端がずれないよう、最後のタイルで行の幅ちょうどに合わせる。
    const others = tiles.slice(0, -1).reduce((sum, tile) => sum + tile.width, 0)
    tiles[tiles.length - 1].width = containerWidth - others - gap * (tiles.length - 1)
  }
  return { height, tiles }
}

/**
 * 行揃え(justified rows)の配置。左から順に目標の高さで詰め、行の幅を超えたらその行を確定して、
 * 行の幅ちょうどになるよう同じ倍率で拡大・縮小する。超えた1枚を入れて縮めるのと、入れずに
 * 広げるのとで、目標の高さに近い方を選ぶ(広げるのは `maxScale` 倍まで)。1枚だけで行の幅を
 * 超える横長はその1枚で1行にする。最後の行は引き伸ばさず、目標の高さのまま左に寄せる。
 * 幅が分からない(0 以下)ときは、全部を目標の高さで1行に置く。
 */
export function layoutJustifiedRows(
  aspects: readonly number[],
  containerWidth: number,
  options: JustifiedRowsOptions,
): JustifiedRow[] {
  const { targetHeight, gap } = options
  const maxHeight = targetHeight * (options.maxScale ?? 1.5)
  const all = aspects.map((_, index) => index)
  if (!(containerWidth > 0)) return aspects.length ? [finishRow(all, aspects, targetHeight, 0, gap, false)] : []

  // 行の幅ちょうどにしたときの高さ。
  const fittedHeight = (indexes: number[]) => {
    const sum = indexes.reduce((total, index) => total + aspects[index], 0)
    return (containerWidth - gap * (indexes.length - 1)) / sum
  }

  const rows: JustifiedRow[] = []
  let row: number[] = []
  for (let index = 0; index < aspects.length; index++) {
    const withItem = [...row, index]
    const widthAtTarget =
      withItem.reduce((total, i) => total + aspects[i] * targetHeight, 0) + gap * (withItem.length - 1)
    if (widthAtTarget < containerWidth) {
      row = withItem
      continue
    }
    const shrunk = fittedHeight(withItem)
    const grown = row.length > 0 ? fittedHeight(row) : Infinity
    if (grown <= maxHeight && Math.abs(grown - targetHeight) < Math.abs(shrunk - targetHeight)) {
      // 入れずに広げる方が目標に近い。この1枚は次の行の先頭にする。
      rows.push(finishRow(row, aspects, grown, containerWidth, gap, true))
      row = [index]
      // 次の行の先頭だけで幅を超える(超横長)なら、その1枚で1行にする。
      if (aspects[index] * targetHeight >= containerWidth) {
        rows.push(finishRow(row, aspects, fittedHeight(row), containerWidth, gap, true))
        row = []
      }
    } else {
      rows.push(finishRow(withItem, aspects, shrunk, containerWidth, gap, true))
      row = []
    }
  }
  if (row.length > 0) rows.push(finishRow(row, aspects, targetHeight, containerWidth, gap, false))
  return rows
}

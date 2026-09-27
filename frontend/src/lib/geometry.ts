/** ビューア・マスクエディタで共有する、表示サイズに関する純粋関数。 */
export interface Size {
  width: number
  height: number
}

/**
 * コンテナに収まる表示倍率を返す(アスペクト比を保った contain fit)。
 * 「拡大はしない」= 結果は常に 1 以下にする。
 * コンテナ/コンテンツのどちらかのサイズが 0 以下の場合は 1(等倍)を返す。
 */
export function computeFitScale(container: Size, content: Size): number {
  if (container.width <= 0 || container.height <= 0 || content.width <= 0 || content.height <= 0) {
    return 1
  }
  const scale = Math.min(container.width / content.width, container.height / content.height)
  return Math.min(1, scale)
}

/**
 * マスクエディタ・スケッチエディタで共有する Canvas ユーティリティ。
 * 表示スケール計算(`computeFitScale`)は `lib/geometry.ts` の実装をそのまま再エクスポートする
 * (Viewer 側とロジックを共有するため、実体はそちらに置いたまま)。
 */
import { msg } from '../../i18n'

export { computeFitScale, type Size } from '../../lib/geometry'

/** Canvas の内容を PNG の Blob にする。 */
export function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
}

/** 画像 URL を `HTMLImageElement` として読み込む。 */
export function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error(msg().mask.imageLoadError))
    img.src = src
  })
}

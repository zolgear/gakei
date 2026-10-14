/**
 * 配置の書き出し(ADR-0050 3章)。キャンバスの実寸の使い捨ての WebGL canvas に、画面と同じ
 * 描画の処理(`CompositeRenderer`)で素材を描き、PNG の Blob にする。
 *
 * 素材は原本(`variant=original`)を使う。GPU のメモリを抑えるため、素材を下から1枚ずつ
 * 読み込んで描き、以降の素材で使わないテクスチャはすぐ捨てる(同じ Asset を複製した素材は
 * 1回だけ読む)。
 */
import { assetUrl } from '../../api/assetUrl'
import { canvasToBlob, loadImage } from '../mask/canvasUtils'
import type { EditorDoc } from './recipe'
import { CompositeRenderer, TRANSPARENT, WHITE } from './renderer'

/** 原本のテクスチャの長辺の上限(GPU の上限がこれより小さければそちら)。 */
const EXPORT_TEXTURE_EDGE = 8192

export class CompositeExportError extends Error {
  readonly reason: 'webgl' | 'tooLarge' | 'load' | 'encode'
  constructor(reason: 'webgl' | 'tooLarge' | 'load' | 'encode') {
    super(reason)
    this.reason = reason
  }
}

export async function exportComposite(doc: EditorDoc): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = doc.canvas.width
  canvas.height = doc.canvas.height
  const renderer = CompositeRenderer.create(canvas, { preserveDrawingBuffer: true })
  if (!renderer) throw new CompositeExportError('webgl')
  try {
    if (doc.canvas.width > renderer.maxViewport || doc.canvas.height > renderer.maxViewport) {
      throw new CompositeExportError('tooLarge')
    }
    renderer.clear(doc.background === 'white' ? WHITE : TRANSPARENT)
    const textures = new Map<string, WebGLTexture>()
    const view = { scale: 1, offsetX: 0, offsetY: 0 }
    for (let i = 0; i < doc.layers.length; i += 1) {
      const layer = doc.layers[i]
      let texture = textures.get(layer.assetId)
      if (!texture) {
        let img: HTMLImageElement
        try {
          img = await loadImage(assetUrl(layer.assetId, 'original'))
        } catch {
          throw new CompositeExportError('load')
        }
        texture = renderer.createTexture(img, EXPORT_TEXTURE_EDGE)
        textures.set(layer.assetId, texture)
      }
      renderer.drawLayers([{ texture, corners: layer.quad, flipX: layer.flipX, flipY: layer.flipY }], view)
      if (!doc.layers.slice(i + 1).some((l) => l.assetId === layer.assetId)) {
        renderer.deleteTexture(texture)
        textures.delete(layer.assetId)
      }
    }
    const blob = await canvasToBlob(canvas)
    if (!blob) throw new CompositeExportError('encode')
    return blob
  } finally {
    renderer.dispose()
    renderer.loseContext()
  }
}

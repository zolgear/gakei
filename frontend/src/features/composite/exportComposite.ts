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
import type { Quad, Size } from './quad'
import type { CompositeBackground, CompositeRecipe } from './recipe'
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

/**
 * 書き出しに要るものだけ(編集画面の `EditorDoc` もこの形を満たす)。レシピからも作れるので、
 * 編集画面を開かずに描き直せる(出力サイズを変えたとき。ADR-0050 5章、2026-10-24 追記)。
 */
export interface CompositeExportSpec {
  canvas: Size
  background: CompositeBackground
  /** 下から上への重ね順。 */
  layers: readonly { assetId: string; quad: Quad; flipX: boolean; flipY: boolean }[]
}

/** 保存したレシピを書き出しの形にする。 */
export function exportSpecFromRecipe(recipe: CompositeRecipe): CompositeExportSpec {
  return {
    canvas: { width: recipe.canvas.width, height: recipe.canvas.height },
    background: recipe.canvas.background === 'transparent' ? 'transparent' : 'white',
    layers: recipe.layers.map((l) => {
      const c = l.corners
      const quad: Quad = [
        { x: c[0][0], y: c[0][1] },
        { x: c[1][0], y: c[1][1] },
        { x: c[2][0], y: c[2][1] },
        { x: c[3][0], y: c[3][1] },
      ]
      return { assetId: l.source_asset_id, quad, flipX: l.flip_x, flipY: l.flip_y }
    }),
  }
}

export async function exportComposite(doc: CompositeExportSpec): Promise<Blob> {
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

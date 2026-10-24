/**
 * 配置のキャンバスの大きさを変えるときの素材の置き直し(ADR-0050 5章、2026-10-24 追記)。純粋関数。
 *
 * 配置は出力の一枚絵の下絵なので、キャンバスは生成のフォームの出力サイズに従う。大きさを変えたら、
 * 素材ごとに、大きさ(px)は縦横の倍率の小さい方で拡大縮小し、中心(四隅の重心)はキャンバスに
 * 対する割合のまま移す。四隅の形・回転・反転はそのまま(反転はテクスチャの側なので触らない)。
 */
import { quadCenter, type Quad, type Size } from './quad'
import type { CompositeRecipe } from './recipe'

export function sameSize(a: Size | null | undefined, b: Size | null | undefined): boolean {
  return Boolean(a && b && a.width === b.width && a.height === b.height)
}

/** 素材1枚の四隅を、`from` のキャンバスから `to` のキャンバスへ移す。 */
export function rescaleQuadToCanvas(quad: Quad, from: Size, to: Size): Quad {
  if (sameSize(from, to)) return quad
  const s = Math.min(to.width / from.width, to.height / from.height)
  const c = quadCenter(quad)
  const nx = (c.x / from.width) * to.width
  const ny = (c.y / from.height) * to.height
  const map = (p: { x: number; y: number }) => ({ x: nx + (p.x - c.x) * s, y: ny + (p.y - c.y) * s })
  return [map(quad[0]), map(quad[1]), map(quad[2]), map(quad[3])]
}

function round2(n: number): number {
  return Math.round(n * 100) / 100
}

/**
 * レシピのキャンバスを `size` に変えた新しいレシピを返す(引数は書き換えない)。四隅は送る形に
 * 合わせて小数2桁に丸める。大きさが同じならそのまま返す。
 */
export function resizeRecipe(recipe: CompositeRecipe, size: Size): CompositeRecipe {
  const from = { width: recipe.canvas.width, height: recipe.canvas.height }
  if (sameSize(from, size)) return recipe
  return {
    ...recipe,
    canvas: { ...recipe.canvas, width: size.width, height: size.height },
    layers: recipe.layers.map((layer) => {
      const c = layer.corners
      const quad: Quad = [
        { x: c[0][0], y: c[0][1] },
        { x: c[1][0], y: c[1][1] },
        { x: c[2][0], y: c[2][1] },
        { x: c[3][0], y: c[3][1] },
      ]
      const moved = rescaleQuadToCanvas(quad, from, size)
      return { ...layer, corners: moved.map((p) => [round2(p.x), round2(p.y)] as [number, number]) }
    }),
  }
}

/**
 * 配置の編集画面を開いたときのキャンバスの大きさ。出力サイズ(`formSize`。auto などで決まらなければ
 * null)を優先し、無ければ、開き直しならレシピの大きさ、新しく始めるなら1枚目の素材の大きさ、
 * それも無ければ 1024×1024。どれも1辺の上限に収めた後の値を渡す。
 */
export function initialCanvasSize(args: {
  formSize: Size | null
  recipeCanvas?: Size | null
  firstSourceSize?: Size | null
}): Size {
  return args.formSize ?? args.recipeCanvas ?? args.firstSourceSize ?? { width: 1024, height: 1024 }
}

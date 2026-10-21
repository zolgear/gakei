/**
 * 四角と候補の名前(画像の上の文字)の置き場所を決める純粋関数(ADR-0053)。近い四角の名前が
 * 重なって読めなくならないよう、重なるときは上下にずらす(簡単な貪欲法。順に置き、先に置いた名前と
 * 避けたい物(番号のバッジなど)に重ならない最初の位置を使う。どこも重なるなら元の位置)。
 */

export interface CaptionRect {
  x: number
  y: number
  width: number
  height: number
}

export interface CaptionRequest extends CaptionRect {
  key: string
}

/** 文字の幅の目安(全角は文字の大きさ、半角はその 0.6 倍)。 */
export function estimateTextWidth(text: string, fontSize: number): number {
  let width = 0
  for (const ch of text) {
    const code = ch.codePointAt(0) ?? 0
    width += code >= 0x2e80 ? fontSize : fontSize * 0.6
  }
  return width
}

function overlaps(a: CaptionRect, b: CaptionRect): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

/** ずらす段の数(上下それぞれ)。 */
const MAX_STEPS = 4

/**
 * 名前を置く位置(左上)を、`requests` の順に決める。`bounds` は画像の大きさで、名前はその中に収める。
 * `obstacles` は避けたい物(重なっても置くが、避けられるなら避ける)。
 */
export function placeCaptions(
  requests: readonly CaptionRequest[],
  bounds: { width: number; height: number },
  obstacles: readonly CaptionRect[] = [],
): Map<string, { x: number; y: number }> {
  const placed: CaptionRect[] = []
  const result = new Map<string, { x: number; y: number }>()
  const clampX = (x: number, w: number) => Math.min(Math.max(0, x), Math.max(0, bounds.width - w))
  const clampY = (y: number, h: number) => Math.min(Math.max(0, y), Math.max(0, bounds.height - h))
  for (const req of requests) {
    const x = clampX(req.x, req.width)
    const step = req.height + 1
    const tries = [0]
    for (let i = 1; i <= MAX_STEPS; i += 1) tries.push(i * step, -i * step)
    let chosen: CaptionRect | null = null
    for (const dy of tries) {
      const rect = { x, y: clampY(req.y + dy, req.height), width: req.width, height: req.height }
      if (!placed.some((p) => overlaps(p, rect)) && !obstacles.some((o) => overlaps(o, rect))) {
        chosen = rect
        break
      }
    }
    const rect = chosen ?? { x, y: clampY(req.y, req.height), width: req.width, height: req.height }
    placed.push(rect)
    result.set(req.key, { x: rect.x, y: rect.y })
  }
  return result
}

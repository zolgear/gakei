/**
 * 差し込み先の図の配置(ADR-0052 4章)。層別(左 → 右、または上 → 下)を自前で計算する純粋関数。配置は保存しない。
 *
 * 1. 段: 入力側からの最長の道の長さ。そのあと、後ろにつながる箱は「後ろの箱の最小の段 - 1」まで
 *    右へ寄せる(読み込みのノードが左端に固まって、長い配線が横切らないように)。
 * 2. 段の中の並び: 前後の箱の位置の平均(重心)で、左右に数回並べ替える。背骨(`spine`)の箱は段の先頭、
 *    `sink` の箱(使われない束など)は段の下へ。
 * 3. 座標: 段ごとに、前の箱の中心の平均に合わせて縦に置き、重ならないように下へずらす。
 *    最初の段は後ろの箱に合わせて置き直す。
 */

export interface LayoutBox {
  id: string
  width: number
  height: number
  /** 段の下に置く(使われない束など)。 */
  sink?: boolean
}

export interface LayoutEdge {
  from: string
  to: string
}

export interface LayoutOptions {
  /**
   * 向き。`LR` は段を左 → 右に並べる(既定)。`TB` は段を上 → 下に並べる(縦長の枠向け)。
   * `TB` のときの `columnGap` は段の間(縦)、`rowGap` は段の中の箱の間(横)。
   */
  direction?: 'LR' | 'TB'
  /**
   * 背骨にする箱(主要な道筋。入力 → 出力の順)。各段の先頭(上、`TB` では左)に置き、背骨の前の箱に
   * そろえて一直線にする(プロンプトの根 → サンプラー → 保存を、まとめて見られるように)。
   */
  spine?: readonly string[]
  columnGap?: number
  rowGap?: number
  padding?: number
}

export interface LayoutResult {
  positions: Map<string, { x: number; y: number }>
  layers: Map<string, number>
  width: number
  height: number
}

export function layoutLayers(
  boxes: readonly LayoutBox[],
  edges: readonly LayoutEdge[],
  options: LayoutOptions = {},
): LayoutResult {
  if (options.direction === 'TB') {
    // 幅と高さ、x と y を入れ替えて左 → 右で並べ、戻す。
    const swapped = layoutLayers(
      boxes.map((b) => ({ ...b, width: b.height, height: b.width })),
      edges,
      { ...options, direction: 'LR' },
    )
    const positions = new Map([...swapped.positions].map(([id, p]) => [id, { x: p.y, y: p.x }]))
    return { positions, layers: swapped.layers, width: swapped.height, height: swapped.width }
  }
  const columnGap = options.columnGap ?? 56
  const rowGap = options.rowGap ?? 16
  const padding = options.padding ?? 16
  const positions = new Map<string, { x: number; y: number }>()
  const layers = new Map<string, number>()
  if (boxes.length === 0) return { positions, layers, width: 0, height: 0 }

  const byId = new Map(boxes.map((b) => [b.id, b]))
  const spine = new Set((options.spine ?? []).filter((id) => byId.has(id)))
  const preds = new Map<string, string[]>(boxes.map((b) => [b.id, []]))
  const succs = new Map<string, string[]>(boxes.map((b) => [b.id, []]))
  const seenEdge = new Set<string>()
  for (const e of edges) {
    if (e.from === e.to || !byId.has(e.from) || !byId.has(e.to)) continue
    const key = `${e.from}\u0000${e.to}`
    if (seenEdge.has(key)) continue
    seenEdge.add(key)
    succs.get(e.from)?.push(e.to)
    preds.get(e.to)?.push(e.from)
  }

  // 1. 段(循環があっても止まるように、辿り中の箱は 0 とみなす)。
  const visiting = new Set<string>()
  const visit = (id: string): number => {
    const known = layers.get(id)
    if (known !== undefined) return known
    if (visiting.has(id)) return 0
    visiting.add(id)
    let layer = 0
    for (const p of preds.get(id) ?? []) layer = Math.max(layer, visit(p) + 1)
    visiting.delete(id)
    layers.set(id, layer)
    return layer
  }
  for (const b of boxes) visit(b.id)
  const byLayerDesc = [...boxes].sort((a, b) => (layers.get(b.id) ?? 0) - (layers.get(a.id) ?? 0))
  for (const b of byLayerDesc) {
    const s = succs.get(b.id) ?? []
    if (s.length === 0) continue
    const minSucc = Math.min(...s.map((x) => layers.get(x) ?? 0))
    layers.set(b.id, Math.max(layers.get(b.id) ?? 0, minSucc - 1))
  }
  const layerCount = Math.max(...boxes.map((b) => layers.get(b.id) ?? 0)) + 1
  const columns: LayoutBox[][] = Array.from({ length: layerCount }, () => [])
  for (const b of boxes) columns[layers.get(b.id) ?? 0].push(b)

  // 2. 並び(重心法)。
  const order = new Map<string, number>()
  columns.forEach((col) => col.forEach((b, i) => order.set(b.id, i)))
  const barycenter = (id: string, neighbors: Map<string, string[]>) => {
    const xs = (neighbors.get(id) ?? []).map((n) => order.get(n) ?? 0)
    return xs.length > 0 ? xs.reduce((a, v) => a + v, 0) / xs.length : (order.get(id) ?? 0)
  }
  for (let iteration = 0; iteration < 6; iteration += 1) {
    const forward = iteration % 2 === 0
    const sequence = forward ? columns : [...columns].reverse()
    const neighbors = forward ? preds : succs
    for (const col of sequence) {
      const keys = new Map(col.map((b) => [b.id, barycenter(b.id, neighbors)]))
      col.sort(
        (a, b) =>
          Number(!spine.has(a.id)) - Number(!spine.has(b.id)) ||
          Number(a.sink === true) - Number(b.sink === true) ||
          (keys.get(a.id) ?? 0) - (keys.get(b.id) ?? 0) ||
          a.id.localeCompare(b.id),
      )
      col.forEach((b, i) => order.set(b.id, i))
    }
  }

  // 3. 座標。
  const columnX: number[] = []
  let x = padding
  for (const col of columns) {
    columnX.push(x)
    const width = Math.max(0, ...col.map((b) => b.width))
    x += width + columnGap
  }
  const centerY = new Map<string, number>()
  const placeColumn = (col: LayoutBox[], index: number, neighbors: Map<string, string[]>) => {
    let bottom = Number.NEGATIVE_INFINITY
    for (const b of col) {
      let placed = (neighbors.get(b.id) ?? []).filter((n) => centerY.has(n))
      // 背骨の箱は、背骨の前後の箱にだけそろえる(一直線にする)。
      if (spine.has(b.id) && placed.some((n) => spine.has(n))) placed = placed.filter((n) => spine.has(n))
      const want =
        placed.length > 0
          ? placed.reduce((a, n) => a + (centerY.get(n) ?? 0), 0) / placed.length - b.height / 2
          : bottom === Number.NEGATIVE_INFINITY
            ? 0
            : bottom + rowGap
      const y = bottom === Number.NEGATIVE_INFINITY ? want : Math.max(want, bottom + rowGap)
      positions.set(b.id, { x: columnX[index], y })
      centerY.set(b.id, y + b.height / 2)
      bottom = y + b.height
    }
  }
  columns.forEach((col, i) => placeColumn(col, i, preds))
  if (columns.length > 1) {
    for (const b of columns[0]) centerY.delete(b.id)
    placeColumn(columns[0], 0, succs)
  }
  const minY = Math.min(...[...positions.values()].map((p) => p.y))
  for (const p of positions.values()) p.y += padding - minY
  const width = x - columnGap + padding
  const height = Math.max(...boxes.map((b) => (positions.get(b.id)?.y ?? 0) + b.height)) + padding
  return { positions, layers, width, height }
}

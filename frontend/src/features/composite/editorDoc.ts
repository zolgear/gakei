/**
 * 配置の編集画面の状態(`EditorDoc`)を変える純粋関数と、元に戻す・やり直すの履歴。
 * どの関数も新しい値を返し、引数を書き換えない。
 */
import {
  centerQuadOnCanvas,
  fitQuadToCanvas,
  initialLayout,
  rectQuad,
  similarityQuad,
  translateQuad,
  type Quad,
  type Size,
} from './quad'
import { MAX_LAYERS, type CompositeBackground, type EditorDoc, type EditorLayer, type ParsedComposition } from './recipe'

let keyCounter = 0
export function newLayerKey(): string {
  keyCounter += 1
  return `layer-${Date.now().toString(36)}-${keyCounter.toString(36)}`
}

export interface LayerSource {
  assetId: string
  natural: Size
}

/** 素材を横に並べた状態から始める(ADR-0050 5章)。上限を超えた分は捨てる。 */
export function createDocFromSources(sources: readonly LayerSource[], canvas: Size): EditorDoc {
  const used = sources.slice(0, MAX_LAYERS)
  const quads = initialLayout(
    used.map((s) => s.natural),
    canvas,
  )
  return {
    canvas,
    background: 'white',
    layers: used.map((s, i) => ({
      key: newLayerKey(),
      assetId: s.assetId,
      natural: s.natural,
      quad: quads[i],
      flipX: false,
      flipY: false,
      addedSeq: i,
    })),
    primaryAssetId: used[0]?.assetId ?? null,
  }
}

/** 保存したレシピから作り直す。`naturals` に無い素材(大きさが分からない)も外す。 */
export function createDocFromComposition(
  parsed: ParsedComposition,
  naturals: ReadonlyMap<string, Size>,
): { doc: EditorDoc; droppedCount: number } {
  let dropped = parsed.droppedCount
  const layers: EditorLayer[] = []
  // 主たる素材を足した順の最初にし、残りは重ね順のまま。
  let seq = 1
  for (const l of parsed.layers) {
    const natural = naturals.get(l.assetId)
    if (!natural) {
      dropped += 1
      continue
    }
    layers.push({
      key: newLayerKey(),
      assetId: l.assetId,
      natural,
      quad: l.quad,
      flipX: l.flipX,
      flipY: l.flipY,
      addedSeq: l.assetId === parsed.primaryAssetId ? 0 : seq++,
    })
  }
  return {
    doc: {
      canvas: parsed.canvas,
      background: parsed.background,
      layers: layers.slice(0, MAX_LAYERS),
      primaryAssetId: parsed.primaryAssetId || null,
    },
    droppedCount: dropped,
  }
}

function nextSeq(doc: EditorDoc): number {
  return doc.layers.reduce((m, l) => Math.max(m, l.addedSeq), -1) + 1
}

/**
 * 素材を足す(一番上に重ねる)。キャンバスの半分ほどに収めて中央に置き、2枚目以降は少しずつずらす。
 * 上限を超えた分は足さず、足せなかった数を返す。
 */
export function addLayers(doc: EditorDoc, sources: readonly LayerSource[]): { doc: EditorDoc; added: EditorLayer[]; rejectedCount: number } {
  const room = Math.max(0, MAX_LAYERS - doc.layers.length)
  const toAdd = sources.slice(0, room)
  let seq = nextSeq(doc)
  const step = Math.min(doc.canvas.width, doc.canvas.height) * 0.04
  const added = toAdd.map((s, i) => {
    const scale = Math.min((doc.canvas.width * 0.5) / s.natural.width, (doc.canvas.height * 0.5) / s.natural.height)
    const quad = rectQuad(
      doc.canvas.width / 2 + step * i,
      doc.canvas.height / 2 + step * i,
      s.natural.width * scale,
      s.natural.height * scale,
    )
    return { key: newLayerKey(), assetId: s.assetId, natural: s.natural, quad, flipX: false, flipY: false, addedSeq: seq++ }
  })
  const layers = [...doc.layers, ...added]
  return {
    doc: { ...doc, layers, primaryAssetId: doc.primaryAssetId ?? added[0]?.assetId ?? null },
    added,
    rejectedCount: sources.length - toAdd.length,
  }
}

function updateLayer(doc: EditorDoc, key: string, f: (l: EditorLayer) => EditorLayer): EditorDoc {
  return { ...doc, layers: doc.layers.map((l) => (l.key === key ? f(l) : l)) }
}

export function setLayerQuad(doc: EditorDoc, key: string, quad: Quad): EditorDoc {
  return updateLayer(doc, key, (l) => ({ ...l, quad }))
}

export function removeLayer(doc: EditorDoc, key: string): EditorDoc {
  return { ...doc, layers: doc.layers.filter((l) => l.key !== key) }
}

/** 複製(すぐ上に重ね、少しずらす)。上限なら何もしない。 */
export function duplicateLayer(doc: EditorDoc, key: string): { doc: EditorDoc; newKey: string | null } {
  if (doc.layers.length >= MAX_LAYERS) return { doc, newKey: null }
  const index = doc.layers.findIndex((l) => l.key === key)
  if (index === -1) return { doc, newKey: null }
  const src = doc.layers[index]
  const step = Math.min(doc.canvas.width, doc.canvas.height) * 0.03
  const copy: EditorLayer = { ...src, key: newLayerKey(), quad: translateQuad(src.quad, step, step), addedSeq: nextSeq(doc) }
  const layers = doc.layers.slice()
  layers.splice(index + 1, 0, copy)
  return { doc: { ...doc, layers }, newKey: copy.key }
}

/** 重ね順を1つ上(前面)・下(背面)へ。端なら何もしない。 */
export function moveLayer(doc: EditorDoc, key: string, direction: 'forward' | 'backward'): EditorDoc {
  const index = doc.layers.findIndex((l) => l.key === key)
  const target = direction === 'forward' ? index + 1 : index - 1
  if (index === -1 || target < 0 || target >= doc.layers.length) return doc
  const layers = doc.layers.slice()
  const [moved] = layers.splice(index, 1)
  layers.splice(target, 0, moved)
  return { ...doc, layers }
}

export function toggleFlip(doc: EditorDoc, key: string, axis: 'x' | 'y'): EditorDoc {
  return updateLayer(doc, key, (l) => (axis === 'x' ? { ...l, flipX: !l.flipX } : { ...l, flipY: !l.flipY }))
}

export function setPrimary(doc: EditorDoc, assetId: string): EditorDoc {
  return { ...doc, primaryAssetId: assetId }
}

export function setBackground(doc: EditorDoc, background: CompositeBackground): EditorDoc {
  return { ...doc, background }
}

export function fitLayer(doc: EditorDoc, key: string): EditorDoc {
  return updateLayer(doc, key, (l) => ({ ...l, quad: fitQuadToCanvas(l.quad, doc.canvas) }))
}

export function centerLayer(doc: EditorDoc, key: string): EditorDoc {
  return updateLayer(doc, key, (l) => ({ ...l, quad: centerQuadOnCanvas(l.quad, doc.canvas) }))
}

/**
 * キャンバスの大きさを変える。素材は全体の並びを保ったまま、新しいキャンバスに合わせて
 * 一様に拡大縮小し(縦横の倍率の小さい方)、中心を新しいキャンバスの中心へ動かす。
 */
export function resizeCanvas(doc: EditorDoc, canvas: Size): EditorDoc {
  const old = doc.canvas
  if (old.width === canvas.width && old.height === canvas.height) return doc
  const s = Math.min(canvas.width / old.width, canvas.height / old.height)
  const pivot = { x: old.width / 2, y: old.height / 2 }
  const dx = canvas.width / 2 - pivot.x
  const dy = canvas.height / 2 - pivot.y
  return {
    ...doc,
    canvas,
    layers: doc.layers.map((l) => ({ ...l, quad: similarityQuad(l.quad, pivot, s, 0, dx, dy) })),
  }
}

export function nudgeLayer(doc: EditorDoc, key: string, dx: number, dy: number): EditorDoc {
  return updateLayer(doc, key, (l) => ({ ...l, quad: translateQuad(l.quad, dx, dy) }))
}

// -- 元に戻す・やり直す -------------------------------------------------------------

export const MAX_HISTORY = 100

export interface History<T> {
  past: T[]
  present: T
  future: T[]
}

export function initHistory<T>(present: T): History<T> {
  return { past: [], present, future: [] }
}

/** 新しい状態を積む(やり直しの先は捨てる)。同じ値なら何もしない。 */
export function pushHistory<T>(h: History<T>, next: T): History<T> {
  if (next === h.present) return h
  const past = [...h.past, h.present]
  if (past.length > MAX_HISTORY) past.shift()
  return { past, present: next, future: [] }
}

export function undoHistory<T>(h: History<T>): History<T> {
  if (h.past.length === 0) return h
  const past = h.past.slice(0, -1)
  return { past, present: h.past[h.past.length - 1], future: [h.present, ...h.future] }
}

export function redoHistory<T>(h: History<T>): History<T> {
  if (h.future.length === 0) return h
  const [next, ...future] = h.future
  return { past: [...h.past, h.present], present: next, future }
}

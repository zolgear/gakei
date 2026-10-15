/**
 * 配置のレシピ(ADR-0050 3章)と、編集画面の状態(素材の並び)との変換。
 *
 * レシピは「何をどう置いたか」の記録で、サーバーは画像を描き直さない。素材ごとの四隅は
 * キャンバスの px で、画像の左上・右上・右下・左下の順。`layers` は下から上への重ね順。
 * 反転はこの4点ではなく、四角形の中でテクスチャを鏡写しにする。
 */
import type { components } from '../../api/schema'
import { isValidQuad, type Quad, type Size } from './quad'

/** 作り方の版。1 は四隅を射影変換で描いたもの、2 は双一次の写像で描いたもの(ADR-0050 1章)。 */
export const RECIPE_VERSION = 2
export const MAX_LAYERS = 16
/** キャンバスの1辺の範囲(サーバーの画像の大きさの上限に合わせる)。 */
export const MIN_CANVAS_EDGE = 64
export const MAX_CANVAS_EDGE = 3840
/** 四隅の座標の範囲(キャンバスの幅・高さの何倍まで)。サーバーの検証と同じ。 */
export const CORNER_LIMIT_FACTOR = 4

/** レシピの型はサーバーの OpenAPI から生成したもの(`CompositionRecipe`)をそのまま使う。 */
export type CompositeRecipe = components['schemas']['CompositionRecipe']
export type CompositeRecipeLayer = components['schemas']['CompositionLayer']
/** `GET /api/assets/{id}` の `composition`(kind=composite のときだけ)。 */
export type CompositionDetail = components['schemas']['AssetComposition']
/** 1辺の上限に収まるよう縮める(縦横比を保つ)。 */
export function clampCanvasSize(size: Size): Size {
  const s = Math.min(1, MAX_CANVAS_EDGE / Math.max(size.width, size.height))
  return {
    width: Math.max(MIN_CANVAS_EDGE, Math.round(size.width * s)),
    height: Math.max(MIN_CANVAS_EDGE, Math.round(size.height * s)),
  }
}

export type CompositeBackground = CompositeRecipe['canvas']['background']

/** 編集画面の素材1枚。`key` は画面の中だけの識別子(同じ Asset を複製しても別の素材)。 */
export interface EditorLayer {
  key: string
  assetId: string
  /** 素材の元の大きさ(数値の入力の倍率に使う)。 */
  natural: Size
  quad: Quad
  flipX: boolean
  flipY: boolean
  /** 足した順(小さいほど先)。主たる素材の既定と、系列の position の順に使う。 */
  addedSeq: number
}

export interface EditorDoc {
  canvas: Size
  background: CompositeBackground
  /** 下から上への重ね順。 */
  layers: EditorLayer[]
  /** 「主にする」で選んだ Asset。素材から外れたら、足した順の最初に戻る。 */
  primaryAssetId: string | null
}

/** 実際に主たる素材になる Asset(選んだものが素材に無ければ、足した順の最初)。 */
export function effectivePrimaryAssetId(doc: EditorDoc): string | null {
  if (doc.primaryAssetId && doc.layers.some((l) => l.assetId === doc.primaryAssetId)) return doc.primaryAssetId
  let first: EditorLayer | null = null
  for (const l of doc.layers) if (!first || l.addedSeq < first.addedSeq) first = l
  return first?.assetId ?? null
}

/**
 * 素材にした Asset の id を、主たる素材を先頭、残りを足した順に、重複なしで返す
 * (保存後に入力をどう並べるか、どれを入力から外すかに使う)。
 */
export function sourceAssetIdsInOrder(doc: EditorDoc): string[] {
  const primary = effectivePrimaryAssetId(doc)
  const ordered = [...doc.layers].sort((a, b) => a.addedSeq - b.addedSeq).map((l) => l.assetId)
  const out: string[] = []
  for (const id of primary ? [primary, ...ordered] : ordered) if (!out.includes(id)) out.push(id)
  return out
}

function round2(n: number): number {
  return Math.round(n * 100) / 100
}

export type RecipeProblem = 'noLayers' | 'tooManyLayers' | 'invalidShape' | 'outOfBounds' | 'invalidCanvas'

/** 保存できない理由(無ければ null)。 */
export function recipeProblem(doc: EditorDoc): RecipeProblem | null {
  const { width, height } = doc.canvas
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < MIN_CANVAS_EDGE ||
    height < MIN_CANVAS_EDGE ||
    width > MAX_CANVAS_EDGE ||
    height > MAX_CANVAS_EDGE
  ) {
    return 'invalidCanvas'
  }
  if (doc.layers.length === 0) return 'noLayers'
  if (doc.layers.length > MAX_LAYERS) return 'tooManyLayers'
  // 送るのは丸めた座標なので、丸めた後の形で確かめる。
  if (doc.layers.some((l) => !isValidQuad(roundQuad(l.quad)))) return 'invalidShape'
  // サーバーは四隅がキャンバスから遠く離れた素材を受け付けない(|x| ≤ 4×幅、|y| ≤ 4×高さ)。
  if (
    doc.layers.some((l) =>
      l.quad.some((p) => Math.abs(p.x) > width * CORNER_LIMIT_FACTOR || Math.abs(p.y) > height * CORNER_LIMIT_FACTOR),
    )
  ) {
    return 'outOfBounds'
  }
  return null
}

function roundQuad(q: Quad): Quad {
  return [
    { x: round2(q[0].x), y: round2(q[0].y) },
    { x: round2(q[1].x), y: round2(q[1].y) },
    { x: round2(q[2].x), y: round2(q[2].y) },
    { x: round2(q[3].x), y: round2(q[3].y) },
  ]
}

/** 編集画面の状態からレシピを作る。保存できない状態なら例外にせず null(先に `recipeProblem` を見る)。 */
export function buildRecipe(doc: EditorDoc): CompositeRecipe | null {
  if (recipeProblem(doc)) return null
  const primary = effectivePrimaryAssetId(doc)
  if (!primary) return null
  return {
    version: RECIPE_VERSION,
    canvas: { width: doc.canvas.width, height: doc.canvas.height, background: doc.background },
    primary_source_asset_id: primary,
    layers: doc.layers.map((l) => ({
      source_asset_id: l.assetId,
      corners: roundQuad(l.quad).map((p) => [p.x, p.y] as [number, number]),
      flip_x: l.flipX,
      flip_y: l.flipY,
    })),
  }
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function parseCorners(v: unknown): Quad | null {
  if (!Array.isArray(v) || v.length !== 4) return null
  const pts = v.map((p) =>
    Array.isArray(p) && p.length === 2 && typeof p[0] === 'number' && typeof p[1] === 'number'
      ? { x: p[0], y: p[1] }
      : null,
  )
  if (pts.some((p) => p === null)) return null
  return pts as unknown as Quad
}

export interface ParsedComposition {
  canvas: Size
  background: CompositeBackground
  primaryAssetId: string
  layers: { assetId: string; quad: Quad; flipX: boolean; flipY: boolean }[]
  /** 読めない(論理削除された)素材を外した数。 */
  droppedCount: number
}

/**
 * 作り直し(ADR-0050 3章)のために、保存されたレシピを読む。`sources` で available=false の
 * 素材と、形の壊れた素材は外し、外した数を返す。読めない形なら null。
 */
export function parseComposition(composition: unknown): ParsedComposition | null {
  if (!isRecord(composition) || !isRecord(composition.recipe)) return null
  const recipe = composition.recipe
  if (!isRecord(recipe.canvas) || !Array.isArray(recipe.layers)) return null
  const width = recipe.canvas.width
  const height = recipe.canvas.height
  if (typeof width !== 'number' || typeof height !== 'number' || width <= 0 || height <= 0) return null
  const background: CompositeBackground = recipe.canvas.background === 'transparent' ? 'transparent' : 'white'
  const unavailable = new Set<string>()
  if (Array.isArray(composition.sources)) {
    for (const s of composition.sources) {
      if (isRecord(s) && typeof s.asset_id === 'string' && s.available === false) unavailable.add(s.asset_id)
    }
  }
  const layers: ParsedComposition['layers'] = []
  let droppedCount = 0
  for (const raw of recipe.layers) {
    if (!isRecord(raw) || typeof raw.source_asset_id !== 'string') {
      droppedCount += 1
      continue
    }
    const quad = parseCorners(raw.corners)
    if (!quad || unavailable.has(raw.source_asset_id)) {
      droppedCount += 1
      continue
    }
    layers.push({ assetId: raw.source_asset_id, quad, flipX: raw.flip_x === true, flipY: raw.flip_y === true })
  }
  const primary = typeof recipe.primary_source_asset_id === 'string' ? recipe.primary_source_asset_id : ''
  return { canvas: { width, height }, background, primaryAssetId: primary, layers, droppedCount }
}

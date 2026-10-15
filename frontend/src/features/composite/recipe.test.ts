import { describe, expect, it } from 'vitest'
import {
  addLayers,
  createDocFromComposition,
  createDocFromSources,
  duplicateLayer,
  initHistory,
  moveLayer,
  pushHistory,
  redoHistory,
  removeLayer,
  resizeCanvas,
  setPrimary,
  toggleFlip,
  undoHistory,
} from './editorDoc'
import { quadBounds, rectQuad } from './quad'
import {
  buildRecipe,
  clampCanvasSize,
  effectivePrimaryAssetId,
  parseComposition,
  recipeProblem,
  sourceAssetIdsInOrder,
  type EditorDoc,
} from './recipe'

const canvas = { width: 1024, height: 1024 }
const natural = { width: 512, height: 512 }

function docOf(ids: string[]): EditorDoc {
  return createDocFromSources(
    ids.map((assetId) => ({ assetId, natural })),
    canvas,
  )
}

describe('buildRecipe', () => {
  it('レシピの形(版・キャンバス・主たる素材・重ね順・四隅・反転)', () => {
    let doc = docOf(['a', 'b'])
    doc = toggleFlip(doc, doc.layers[1].key, 'x')
    const recipe = buildRecipe(doc)!
    expect(recipe.version).toBe(2)
    expect(recipe.canvas).toEqual({ width: 1024, height: 1024, background: 'white' })
    expect(recipe.primary_source_asset_id).toBe('a')
    expect(recipe.layers.map((l) => l.source_asset_id)).toEqual(['a', 'b'])
    expect(recipe.layers[0].corners).toHaveLength(4)
    expect(recipe.layers[1].flip_x).toBe(true)
    expect(recipe.layers[1].flip_y).toBe(false)
    // JSON に往復できる。
    expect(JSON.parse(JSON.stringify(recipe))).toEqual(recipe)
  })

  it('素材が無い・形が不正・キャンバスが範囲外なら作らない', () => {
    expect(recipeProblem(docOf([]))).toBe('noLayers')
    expect(buildRecipe(docOf([]))).toBeNull()
    const doc = docOf(['a'])
    const broken: EditorDoc = {
      ...doc,
      layers: [
        {
          ...doc.layers[0],
          quad: [
            { x: 0, y: 0 },
            { x: 100, y: 100 },
            { x: 100, y: 0 },
            { x: 0, y: 100 },
          ],
        },
      ],
    }
    expect(recipeProblem(broken)).toBe('invalidShape')
    expect(recipeProblem({ ...doc, canvas: { width: 10, height: 1024 } })).toBe('invalidCanvas')
    expect(
      recipeProblem({ ...doc, layers: [{ ...doc.layers[0], quad: rectQuad(5000, 100, 100, 100) }] }),
    ).toBe('outOfBounds')
    expect(recipeProblem({ ...doc, canvas: { width: 4000, height: 1024 } })).toBe('invalidCanvas')
  })

  it('「主にする」で主たる素材を入れ替え、消えたら足した順の最初に戻る', () => {
    let doc = docOf(['a', 'b', 'c'])
    doc = setPrimary(doc, 'c')
    expect(buildRecipe(doc)!.primary_source_asset_id).toBe('c')
    expect(sourceAssetIdsInOrder(doc)).toEqual(['c', 'a', 'b'])
    doc = removeLayer(doc, doc.layers[2].key)
    expect(effectivePrimaryAssetId(doc)).toBe('a')
  })
})

describe('parseComposition と作り直し', () => {
  const composition = {
    recipe: {
      version: 1,
      canvas: { width: 800, height: 600, background: 'transparent' },
      primary_source_asset_id: 'b',
      layers: [
        { source_asset_id: 'a', corners: [[0, 0], [100, 0], [100, 100], [0, 100]], flip_x: false, flip_y: true },
        { source_asset_id: 'b', corners: [[200, 0], [300, 0], [300, 100], [200, 100]], flip_x: true, flip_y: false },
        { source_asset_id: 'gone', corners: [[0, 0], [10, 0], [10, 10], [0, 10]], flip_x: false, flip_y: false },
      ],
    },
    sources: [
      { asset_id: 'b', available: true },
      { asset_id: 'a', available: true },
      { asset_id: 'gone', available: false },
    ],
  }

  it('読めない素材を外し、その数を返す', () => {
    const parsed = parseComposition(composition)!
    expect(parsed.canvas).toEqual({ width: 800, height: 600 })
    expect(parsed.background).toBe('transparent')
    expect(parsed.layers.map((l) => l.assetId)).toEqual(['a', 'b'])
    expect(parsed.droppedCount).toBe(1)
    expect(parsed.layers[0].flipY).toBe(true)
  })

  it('作り直した状態は重ね順を保ち、主たる素材を足した順の最初にする', () => {
    const parsed = parseComposition(composition)!
    const naturals = new Map([
      ['a', natural],
      ['b', natural],
    ])
    const { doc, droppedCount } = createDocFromComposition(parsed, naturals)
    expect(droppedCount).toBe(1)
    expect(doc.layers.map((l) => l.assetId)).toEqual(['a', 'b'])
    expect(sourceAssetIdsInOrder(doc)).toEqual(['b', 'a'])
    const recipe = buildRecipe(doc)!
    expect(recipe.primary_source_asset_id).toBe('b')
    expect(recipe.layers[1].corners).toEqual(composition.recipe.layers[1].corners)
    // 版 1(射影変換で描いたもの)を作り直すと、今の版(双一次)のレシピになる。
    expect(recipe.version).toBe(2)
  })

  it('版 2 のレシピも同じように読める', () => {
    const parsed = parseComposition({ ...composition, recipe: { ...composition.recipe, version: 2 } })!
    expect(parsed.layers.map((l) => l.assetId)).toEqual(['a', 'b'])
    expect(parsed.droppedCount).toBe(1)
  })

  it('壊れた形は null', () => {
    expect(parseComposition(null)).toBeNull()
    expect(parseComposition({ recipe: { canvas: {}, layers: [] } })).toBeNull()
  })
})

describe('素材の操作', () => {
  it('足す・上限・複製・重ね順', () => {
    let doc = docOf(['a'])
    const added = addLayers(doc, [{ assetId: 'b', natural }])
    doc = added.doc
    expect(doc.layers.map((l) => l.assetId)).toEqual(['a', 'b'])
    const many = addLayers(
      doc,
      Array.from({ length: 20 }, (_, i) => ({ assetId: `x${i}`, natural })),
    )
    expect(many.doc.layers).toHaveLength(16)
    expect(many.rejectedCount).toBe(6)

    const dup = duplicateLayer(doc, doc.layers[0].key)
    expect(dup.doc.layers.map((l) => l.assetId)).toEqual(['a', 'a', 'b'])
    expect(dup.doc.layers[1].key).toBe(dup.newKey)

    const moved = moveLayer(doc, doc.layers[0].key, 'forward')
    expect(moved.layers.map((l) => l.assetId)).toEqual(['b', 'a'])
    expect(moveLayer(moved, moved.layers[1].key, 'forward')).toBe(moved)
  })

  it('キャンバスの大きさを変えると並びを保って収める', () => {
    const doc = docOf(['a'])
    const resized = resizeCanvas(doc, { width: 512, height: 1024 })
    const b = quadBounds(resized.layers[0].quad)
    expect(b.minX).toBeCloseTo(0)
    expect(b.maxX).toBeCloseTo(512)
    expect((b.minY + b.maxY) / 2).toBeCloseTo(512)
  })
})

describe('履歴', () => {
  it('積む・戻す・やり直す', () => {
    let h = initHistory(1)
    h = pushHistory(h, 2)
    h = pushHistory(h, 3)
    h = undoHistory(h)
    expect(h.present).toBe(2)
    h = redoHistory(h)
    expect(h.present).toBe(3)
    h = undoHistory(undoHistory(h))
    expect(h.present).toBe(1)
    expect(undoHistory(h)).toBe(h)
    h = pushHistory(h, 9)
    expect(h.future).toEqual([])
  })

  it('同じ値は積まない', () => {
    const h = initHistory(rectQuad(0, 0, 1, 1))
    expect(pushHistory(h, h.present)).toBe(h)
  })
})

describe('clampCanvasSize', () => {
  it('長辺を上限に収め、縦横比を保つ', () => {
    expect(clampCanvasSize({ width: 7680, height: 4320 })).toEqual({ width: 3840, height: 2160 })
    expect(clampCanvasSize({ width: 1024, height: 1536 })).toEqual({ width: 1024, height: 1536 })
  })
})

import { describe, expect, it } from 'vitest'
import type { RegionBox, RunRegions } from '../../api/client'
import {
  addRegion,
  areRegionBoxesValid,
  boxFromPoints,
  canSaveRegions,
  boxesFromEditorRegions,
  defaultMoveTarget,
  editorRegionsFromBoxes,
  isValidBox,
  moveBox,
  moveRegionOrder,
  promoteCandidate,
  REGIONS_MAX,
  regionBadgeTextColor,
  regionColor,
  regionExtraInputCount,
  regionProblems,
  resizeBox,
  restoreRunRegions,
  setRegionKind,
  stripAppendedText,
  type Box,
  type EditorRegion,
} from './regions'

function box(overrides: Partial<RegionBox> = {}): RegionBox {
  return {
    number: 1,
    kind: 'change',
    box: [0.1, 0.1, 0.4, 0.5],
    source_box: null,
    instruction: '赤い帽子にする',
    text: null,
    reference_asset_id: null,
    ...overrides,
  }
}

function region(overrides: Partial<EditorRegion> = {}): EditorRegion {
  return {
    key: 'k1',
    kind: 'change',
    box: [0.1, 0.1, 0.4, 0.5],
    sourceBox: null,
    instruction: 'x',
    text: '',
    referenceAssetId: null,
    label: '',
    ...overrides,
  }
}

describe('色(サーバーの PALETTE と同じ)', () => {
  it('番号の順に赤・青・緑…を使い、9 個目から繰り返す', () => {
    expect(regionColor(0)).toBe('rgb(230, 25, 75)')
    expect(regionColor(1)).toBe('rgb(0, 130, 200)')
    expect(regionColor(7)).toBe('rgb(145, 30, 180)')
    expect(regionColor(8)).toBe(regionColor(0))
  })

  it('バッジの数字は明るい色(黄・水色)で黒、暗い色で白', () => {
    expect(regionBadgeTextColor(3)).toBe('#000000')
    expect(regionBadgeTextColor(5)).toBe('#000000')
    expect(regionBadgeTextColor(0)).toBe('#ffffff')
    expect(regionBadgeTextColor(7)).toBe('#ffffff')
  })
})

describe('幾何', () => {
  it('2点から範囲に収めた四角を作る', () => {
    expect(boxFromPoints({ x: 0.5, y: 0.8 }, { x: -0.2, y: 0.3 })).toEqual([0, 0.3, 0.5, 0.8])
  })

  it('動かしても画像の外へ出ない', () => {
    expect(moveBox([0.6, 0.6, 0.9, 0.9], 0.5, -1)).toEqual([0.7, 0, 1, 0.30000000000000004])
  })

  it('大きさの変更は反対の辺を越えない', () => {
    const r = resizeBox([0.2, 0.2, 0.4, 0.4], 'nw', { x: 0.9, y: 0.9 })
    expect(r[0]).toBeLessThan(r[2])
    expect(r[1]).toBeLessThan(r[3])
    expect(resizeBox([0.2, 0.2, 0.4, 0.4], 'e', { x: 0.7, y: 0 })).toEqual([0.2, 0.2, 0.7, 0.4])
  })

  it('isValidBox はサーバーと同じ条件', () => {
    expect(isValidBox([0, 0, 1, 1])).toBe(true)
    expect(isValidBox([0.5, 0, 0.5, 1])).toBe(false)
    expect(isValidBox([0, 0, 1.1, 1])).toBe(false)
    expect(isValidBox(null)).toBe(false)
  })

  it('「動かす」の移す先は元と同じ大きさで、画像の中に収まる', () => {
    const source: Box = [0.7, 0.2, 0.95, 0.4]
    const target = defaultMoveTarget(source)
    expect(isValidBox(target)).toBe(true)
    expect(target[2] - target[0]).toBeCloseTo(0.25)
    expect(target).not.toEqual(source)
  })
})

describe('編集の操作', () => {
  it('API の形と行き来でき、番号は並び順で振り直す', () => {
    const regions = editorRegionsFromBoxes([box({ number: 3, instruction: 'b' }), box({ number: 1, instruction: 'a' })])
    expect(regions.map((r) => r.instruction)).toEqual(['a', 'b'])
    const back = boxesFromEditorRegions(regions)
    expect(back.map((b) => [b.number, b.instruction])).toEqual([
      [1, 'a'],
      [2, 'b'],
    ])
  })

  it('描く文字の空は null、「動かす」以外は source_box を送らない', () => {
    const [b] = boxesFromEditorRegions([region({ text: '', sourceBox: [0, 0, 0.1, 0.1] })])
    expect(b.text).toBeNull()
    expect(b.source_box).toBeNull()
  })

  it('名前は前後の空白を除いて送り、空なら null。API の形から戻せる', () => {
    expect(boxesFromEditorRegions([region({ label: '  白い襟 ' })])[0].label).toBe('白い襟')
    expect(boxesFromEditorRegions([region({ label: '  ' })])[0].label).toBeNull()
    expect(editorRegionsFromBoxes([box({ label: '帽子' })])[0].label).toBe('帽子')
    expect(editorRegionsFromBoxes([box()])[0].label).toBe('')
  })

  it('16 個までしか足せない', () => {
    let regions: EditorRegion[] = []
    for (let i = 0; i < REGIONS_MAX + 2; i += 1) regions = addRegion(regions, [0, 0, 0.5, 0.5])
    expect(regions).toHaveLength(REGIONS_MAX)
  })

  it('番号を入れ替える', () => {
    const regions = [region({ key: 'a' }), region({ key: 'b' })]
    expect(moveRegionOrder(regions, 'b', 'up').map((r) => r.key)).toEqual(['b', 'a'])
    expect(moveRegionOrder(regions, 'a', 'up').map((r) => r.key)).toEqual(['a', 'b'])
  })

  it('「動かす」にすると今の四角が元の位置になり、戻すと元の位置を残す', () => {
    const moved = setRegionKind([region()], 'k1', 'move')[0]
    expect(moved.sourceBox).toEqual([0.1, 0.1, 0.4, 0.5])
    expect(moved.box).not.toEqual(moved.sourceBox)
    const back = setRegionKind([moved], 'k1', 'keep')[0]
    expect(back.box).toEqual([0.1, 0.1, 0.4, 0.5])
    expect(back.sourceBox).toBeNull()
  })
})

describe('検証', () => {
  it('指示は任意(位置だけ・位置と名前だけの四角も保存できる)', () => {
    expect(regionProblems(region({ instruction: '  ' }))).toEqual([])
    expect(regionProblems(region({ kind: 'add', instruction: '', label: 'カフス' }))).toEqual([])
    expect(canSaveRegions([region({ instruction: '' }), region({ key: 'k2', kind: 'add', instruction: '' })])).toBe(true)
    expect(regionProblems(region({ instruction: '', text: 'OPEN' }))).toEqual([])
    expect(regionProblems(region({ instruction: '', referenceAssetId: 'a1' }))).toEqual([])
    expect(regionProblems(region({ kind: 'keep', instruction: '' }))).toEqual([])
    expect(regionProblems(region({ kind: 'remove', instruction: '' }))).toEqual([])
  })

  it('文字数の上限と、潰れた四角', () => {
    expect(regionProblems(region({ instruction: 'a'.repeat(2001) }))).toContain('instructionTooLong')
    expect(regionProblems(region({ text: 'a'.repeat(201) }))).toContain('textTooLong')
    expect(regionProblems(region({ label: 'a'.repeat(101) }))).toContain('labelTooLong')
    expect(regionProblems(region({ box: [0.3, 0.3, 0.3, 0.5] }))).toContain('invalidBox')
    expect(regionProblems(region({ kind: 'move', sourceBox: null }))).toContain('invalidSourceBox')
  })

  it('フォームの値は番号の重なりも見る', () => {
    expect(areRegionBoxesValid([box(), box({ number: 2 })])).toBe(true)
    expect(areRegionBoxesValid([box(), box()])).toBe(false)
  })
})

describe('regionExtraInputCount', () => {
  it('四角を描いた画像 1 枚と、入力に無い参照画像を数える(同じ画像は1枚)', () => {
    const boxes = [
      box({ reference_asset_id: 'ref1' }),
      box({ number: 2, reference_asset_id: 'ref1' }),
      box({ number: 3, reference_asset_id: 'in2' }),
      box({ number: 4, reference_asset_id: 'ref2' }),
    ]
    expect(regionExtraInputCount(boxes, ['in1', 'in2'])).toBe(3)
  })

  it('四角が無ければ 0', () => {
    expect(regionExtraInputCount([], ['in1'])).toBe(0)
  })
})

describe('stripAppendedText', () => {
  it('末尾が足した文のときだけ除く', () => {
    expect(stripAppendedText('猫\n\n領域の指示', '\n\n領域の指示')).toBe('猫')
    expect(stripAppendedText('猫(書き換えた)', '\n\n領域の指示')).toBe('猫(書き換えた)')
    expect(stripAppendedText('猫', '')).toBe('猫')
  })
})

describe('restoreRunRegions', () => {
  const record: RunRegions = {
    version: 1,
    delivery: 'image_and_text',
    regions: [
      box({ number: 2, reference_asset_id: 'ref-new', label: 'カップ' }),
      box({ number: 1, reference_asset_id: 'user2' }),
    ],
    appended_text: '\n\nAPPENDED',
    overlay_asset_id: 'overlay',
  }

  it('四角を描いた画像と、サーバーが足した参照画像を外し、プロンプトを戻す', () => {
    const restored = restoreRunRegions({
      prompt: 'user prompt\n\nAPPENDED',
      inputs: [
        { asset_id: 'user1', role: 'image' as const, position: 0 },
        { asset_id: 'user2', role: 'image' as const, position: 1 },
        { asset_id: 'mask1', role: 'mask' as const, position: 0 },
        { asset_id: 'overlay', role: 'image' as const, position: 2 },
        { asset_id: 'ref-new', role: 'image' as const, position: 3 },
      ],
      regions: record,
    })
    expect(restored.prompt).toBe('user prompt')
    expect(restored.inputs.map((i) => i.asset_id)).toEqual(['user1', 'user2', 'mask1'])
    expect(restored.regions.map((r) => r.number)).toEqual([1, 2])
    // 四角の名前も戻す(四角の定義の一部)。
    expect(restored.regions[1].label).toBe('カップ')
    expect(restored.regionsAssetId).toBe('user1')
  })

  it('利用者の入力にある参照画像(四角を描いた画像より前)は残す', () => {
    const restored = restoreRunRegions({
      prompt: 'p',
      inputs: [
        { asset_id: 'ref-new', role: 'image' as const, position: 0 },
        { asset_id: 'overlay', role: 'image' as const, position: 1 },
      ],
      regions: { ...record, regions: [box({ reference_asset_id: 'ref-new' })] },
    })
    expect(restored.inputs.map((i) => i.asset_id)).toEqual(['ref-new'])
  })

  it('四角の無い Run はそのまま', () => {
    const inputs = [{ asset_id: 'a', role: 'image' as const, position: 0 }]
    expect(restoreRunRegions({ prompt: 'p', inputs, regions: null })).toEqual({
      prompt: 'p',
      inputs,
      regions: [],
      regionsAssetId: null,
    })
  })

  it('利用者が書き換えたプロンプト(末尾が違う)は除かない', () => {
    expect(restoreRunRegions({ prompt: 'edited', inputs: [], regions: record }).prompt).toBe('edited')
  })
})

describe('promoteCandidate', () => {
  const candidate = { box: [0.1, 0.2, 0.3, 0.4], label: ' 左のカバン ' }

  it('候補を名前の付いた四角(変える、指示なし)として足す', () => {
    const result = promoteCandidate([box({ number: 1 })], candidate)
    expect(result.added).toBe(true)
    expect(result.number).toBe(2)
    expect(result.regions[1]).toEqual({
      number: 2,
      kind: 'change',
      box: [0.1, 0.2, 0.3, 0.4],
      source_box: null,
      instruction: '',
      text: null,
      reference_asset_id: null,
      label: '左のカバン',
    })
  })

  it('同じ四角があれば足さずにその番号を返す', () => {
    const first = promoteCandidate([], candidate)
    const again = promoteCandidate(first.regions, candidate)
    expect(again.added).toBe(false)
    expect(again.number).toBe(1)
    expect(again.regions).toHaveLength(1)
  })

  it('上限なら足さない', () => {
    const full = Array.from({ length: REGIONS_MAX }, (_, i) => box({ number: i + 1, box: [0, 0, 0.5, 0.5] }))
    const result = promoteCandidate(full, candidate)
    expect(result.added).toBe(false)
    expect(result.number).toBeNull()
  })
})

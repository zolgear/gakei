/**
 * 入力画像の無い Run の四角(ComfyUI への差し込み。ADR-0053 6章。OpenAI の白紙の配置図。5章)のフォームの判断。capabilities から選べる種類・参照画像・
 * 白紙のキャンバスを決め、再実行で入力画像の無い Run の四角を戻す。
 */
import { describe, expect, it } from 'vitest'
import type { OperationCapabilities, ParamDef, RegionBox, RunRegions } from '../../api/client'
import { blankCanvasSize } from '../run-form/dependencies'
import { allowedRegionKinds, regionsCompatible, regionsSupportedFor, restoreRunRegions, REGION_KINDS } from './regions'

function op(overrides: Partial<OperationCapabilities> = {}): OperationCapabilities {
  return {
    operation: 'generate',
    params: [],
    max_input_images: 0,
    min_input_images: 0,
    supports_mask: false,
    requires_mask: false,
    supports_regions: true,
    regions_delivery: 'comfyui_bboxes',
    regions_kinds: ['change', 'add'],
    regions_reference_images: false,
    regions_requires_input_image: false,
    ...overrides,
  }
}

function box(overrides: Partial<RegionBox> = {}): RegionBox {
  return {
    number: 1,
    kind: 'change',
    box: [0.1, 0.1, 0.4, 0.4],
    source_box: null,
    instruction: '',
    text: null,
    reference_asset_id: null,
    label: null,
    ...overrides,
  }
}

describe('regionsSupportedFor', () => {
  it('入力画像の要らない操作は、入力画像の無い Run でも四角を付けられる', () => {
    expect(regionsSupportedFor('generate', op())).toBe(true)
    expect(regionsSupportedFor('edit', op({ operation: 'edit' }))).toBe(true)
  })

  it('入力画像が要る操作(OpenAI)は、入力画像のある Run だけ', () => {
    const openai = op({ regions_delivery: 'image_and_text', regions_requires_input_image: true })
    expect(regionsSupportedFor('generate', openai)).toBe(false)
    expect(regionsSupportedFor('edit', openai)).toBe(true)
  })

  it('OpenAI の生成は、白紙の配置図で送るので入力画像の無い Run でも付けられる(5章)', () => {
    const generate = op({ regions_delivery: 'image_and_text', regions_reference_images: true })
    expect(regionsSupportedFor('generate', generate)).toBe(true)
    expect(allowedRegionKinds(generate)).toEqual(['change', 'add'])
  })

  it('四角を受けない操作では付けない', () => {
    expect(regionsSupportedFor('generate', op({ supports_regions: false }))).toBe(false)
    expect(regionsSupportedFor('generate', undefined)).toBe(false)
  })
})

describe('選べる種類と参照画像', () => {
  it('capabilities の種類だけを、画面の順で返す', () => {
    expect(allowedRegionKinds(op({ regions_kinds: ['add', 'change'] }))).toEqual(['change', 'add'])
    // 古いサーバー(項目が無い・空)では全種類。
    expect(allowedRegionKinds(op({ regions_kinds: [] }))).toEqual(REGION_KINDS)
  })

  it('選べない種類・使えない参照画像の四角があれば、そのままは送れない', () => {
    expect(regionsCompatible([box(), box({ number: 2, kind: 'add' })], op())).toBe(true)
    expect(regionsCompatible([box({ kind: 'keep' })], op())).toBe(false)
    expect(regionsCompatible([box({ reference_asset_id: 'ref' })], op())).toBe(false)
    expect(regionsCompatible([box({ reference_asset_id: 'ref' })], op({ regions_reference_images: true }))).toBe(true)
  })
})

describe('白紙の大きさ', () => {
  const defs: ParamDef[] = [
    { name: 'width', type: 'int', label: 'W', default: 1344, required: false, description: '' },
    { name: 'height', type: 'int', label: 'H', default: 768, required: false, description: '' },
  ]
  const fallback = { width: 1024, height: 1024 }

  it('幅・高さの項目(ComfyUI)があればその値、未指定なら既定値', () => {
    expect(blankCanvasSize(defs, {}, fallback)).toEqual({ width: 1344, height: 768 })
    expect(blankCanvasSize(defs, { width: '512', height: '896' }, fallback)).toEqual({ width: 512, height: 896 })
  })

  it('項目が無いか値が正しくなければ、出力サイズから解いた値', () => {
    expect(blankCanvasSize([], {}, fallback)).toEqual(fallback)
    expect(blankCanvasSize(defs, { width: 'abc' }, fallback)).toEqual(fallback)
  })
})

describe('入力画像の無い Run の四角を戻す', () => {
  const record: RunRegions = {
    version: 1,
    delivery: 'comfyui_bboxes',
    frame: 'output',
    regions: [box({ number: 2, kind: 'add' }), box({ number: 1, label: '猫' })],
    appended_text: '',
    overlay_asset_id: null,
  }

  it('主たる入力が無くても四角を戻す(白紙。主たる入力には付かない)', () => {
    const restored = restoreRunRegions({ prompt: 'a room', inputs: [], regions: record })
    expect(restored.prompt).toBe('a room')
    expect(restored.inputs).toEqual([])
    expect(restored.regions.map((r) => r.number)).toEqual([1, 2])
    expect(restored.regionsAssetId).toBeNull()
  })

  it('OpenAI の入力画像の無い Run は、配置図と参照画像を外し、足した文を除いて白紙に戻す', () => {
    const appended = '\n\n領域の指示: 1枚目の画像は、白紙の上に…'
    const openaiRecord: RunRegions = {
      version: 1,
      delivery: 'image_and_text',
      frame: 'output',
      regions: [box({ number: 1, reference_asset_id: 'ref' }), box({ number: 2, kind: 'add' })],
      appended_text: appended,
      overlay_asset_id: 'layout',
    }
    const inputs = [
      { asset_id: 'layout', role: 'image' as const, position: 0 },
      { asset_id: 'ref', role: 'image' as const, position: 1 },
    ]
    const restored = restoreRunRegions({ prompt: 'a room' + appended, inputs, regions: openaiRecord })
    expect(restored.prompt).toBe('a room')
    expect(restored.inputs).toEqual([])
    expect(restored.regions.map((r) => r.number)).toEqual([1, 2])
    expect(restored.regions[0].reference_asset_id).toBe('ref')
    expect(restored.regionsAssetId).toBeNull()
  })

  it('入力画像のある ComfyUI の Run は、主たる入力に付けて戻す(入力は外さない)', () => {
    const inputs = [{ asset_id: 'img', role: 'image' as const, position: 0 }]
    const restored = restoreRunRegions({ prompt: 'p', inputs, regions: { ...record, frame: 'input' } })
    expect(restored.inputs).toEqual(inputs)
    expect(restored.regionsAssetId).toBe('img')
    expect(restored.regions).toHaveLength(2)
  })
})

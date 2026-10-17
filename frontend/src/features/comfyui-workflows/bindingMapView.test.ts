import { describe, expect, it } from 'vitest'
import type { ComfyNodeInfo } from '../../api/client'
import { mapEdges } from './bindingGraph'
import { MAP_MIN_READABLE_ZOOM, buildMapView, initialViewport, mapNodeHeight } from './bindingMapView'
import { EMPTY_BINDINGS_FORM, type BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'

const wire = (id: string, port = 0) => [id, port]

function node(
  id: string,
  class_type: string,
  inputs: Record<string, unknown>,
  extra: Partial<ComfyNodeInfo> = {},
): ComfyNodeInfo {
  return {
    id,
    class_type,
    title: class_type,
    state: 'live',
    kind: null,
    inputs: Object.entries(inputs).map(([name, value]) => ({ name, value, linked: Array.isArray(value) })),
    ...extra,
  }
}

// SD1.5 の t2i に、使われない枝を1つ足したもの(架空の値)。
const NODES: ComfyNodeInfo[] = [
  node('4', 'CheckpointLoaderSimple', { ckpt_name: 'model.safetensors' }),
  node('6', 'CLIPTextEncode', { text: 'a cat', clip: wire('4', 1) }, { kind: 'encoder' }),
  node('7', 'CLIPTextEncode', { text: 'blurry', clip: wire('4', 1) }, { kind: 'encoder' }),
  node('5', 'EmptyLatentImage', { width: 512, height: 512, batch_size: 1 }),
  node(
    '3',
    'KSampler',
    { seed: 1, steps: 20, cfg: 7, model: wire('4'), positive: wire('6'), negative: wire('7'), latent_image: wire('5') },
    { kind: 'sampler' },
  ),
  node('8', 'VAEDecode', { samples: wire('3'), vae: wire('4', 2) }),
  node('9', 'SaveImage', { images: wire('8'), filename_prefix: 'x' }, { kind: 'save' }),
  node('20', 'LoadImage', { image: 'old.png' }, { state: 'unused', kind: 'image_source' }),
  node('21', 'ImageInvert', { image: wire('20') }, { state: 'unused' }),
]
const EDGES = mapEdges(NODES, null)

const BINDINGS: BindingsFormState = {
  ...EMPTY_BINDINGS_FORM,
  prompt: { node: '6', input: 'text' },
  negativePrompt: { node: '7', input: 'text' },
  seeds: [{ node: '3', input: 'seed' }],
  width: { node: '5', input: 'width' },
  outputs: ['9'],
}

function row(nodeId: string, input: string): ExposedParamRow {
  return {
    enabled: true,
    name: input,
    node: nodeId,
    input,
    type: 'int',
    label: input,
    default: 1,
    minimum: null,
    maximum: null,
    step: null,
    choices: null,
    maxLength: null,
  }
}

function view(overrides: Partial<Parameters<typeof buildMapView>[0]> = {}) {
  return buildMapView({
    nodes: NODES,
    edges: EDGES,
    bindings: BINDINGS,
    exposedRows: [],
    readings: [],
    mode: 'collapsed',
    focusField: null,
    pickField: null,
    selected: null,
    ...overrides,
  })
}

describe('buildMapView', () => {
  it('役割の札を付け、使われないノードは1つの束にする', () => {
    const v = view({ exposedRows: [row('3', 'steps'), row('3', 'cfg'), row('3', 'denoise'), row('3', 'x')] })
    const sampler = v.flowNodes.find((n) => n.id === '3')!
    expect(sampler.data.badges.map((b) => b.tone)).toEqual(['number', 'exposed', 'exposed', 'exposed'])
    // 公開が多いときは2つまで名前を出し、残りをまとめる。
    expect(sampler.data.badges[3].label).toContain('2')
    const unused = v.flowNodes.find((n) => n.data.state === 'unused')!
    expect(unused.data.count).toBe(2)
    expect(unused.id.startsWith('group:')).toBe(true)
    // 配置: 配線は左から右。
    for (const e of v.flowEdges) {
      const a = v.flowNodes.find((n) => n.id === e.source)!
      const b = v.flowNodes.find((n) => n.id === e.target)!
      expect(a.position.x).toBeLessThan(b.position.x)
    }
    expect(v.width).toBeGreaterThan(0)
  })

  it('開いた項目の道筋の箱と辺に印を付ける', () => {
    const v = view({ focusField: 'prompt' })
    const onPath = v.flowNodes.filter((n) => n.data.onPath).map((n) => n.id)
    expect(onPath.sort()).toEqual(['3', '6', '8', '9'])
    expect(v.flowEdges.filter((e) => e.data?.onPath).map((e) => `${e.source}>${e.target}`).sort()).toEqual([
      '3>8',
      '6>3',
      '8>9',
    ])
  })

  it('図で選ぶ間は、選べる箱と選べない箱を分ける', () => {
    const v = view({ pickField: 'seed', mode: 'full' })
    const pickable = v.flowNodes.filter((n) => n.data.pickable === true).map((n) => n.id)
    expect(pickable).toEqual(['3'])
    expect(v.flowNodes.every((n) => n.data.pickable !== null)).toBe(true)
    expect(view().flowNodes.every((n) => n.data.pickable === null)).toBe(true)
  })

  it('選んだノードを含む箱に印を付ける', () => {
    const v = view({ selected: '20' })
    const selected = v.flowNodes.filter((n) => n.data.selected)
    expect(selected).toHaveLength(1)
    expect(selected[0].data.count).toBe(2)
  })

  it('箱の高さは札の行の数で決まる', () => {
    expect(mapNodeHeight([])).toBeLessThan(mapNodeHeight([{ label: 'プロンプト', tone: 'text' }]))
    const many = Array.from({ length: 8 }, () => ({ label: '公開 sampler_name', tone: 'exposed' as const }))
    expect(mapNodeHeight(many)).toBeGreaterThan(mapNodeHeight(many.slice(0, 1)))
  })
})

describe('initialViewport', () => {
  it('収まる図は倍率 1 までで中央に置く', () => {
    expect(initialViewport({ width: 800, height: 400 }, { width: 400, height: 200 })).toEqual({ x: 200, y: 100, zoom: 1 })
  })

  it('収まらない図は読める倍率で止め、左上(入力の側)から見せる', () => {
    const v = initialViewport({ width: 500, height: 400 }, { width: 3000, height: 600 })
    expect(v.zoom).toBe(MAP_MIN_READABLE_ZOOM)
    expect(v.x).toBe(0)
    expect(v.y).toBeGreaterThanOrEqual(0)
  })

  it('大きさが分からないときは倍率 1', () => {
    expect(initialViewport({ width: 0, height: 0 }, { width: 10, height: 10 })).toEqual({ x: 0, y: 0, zoom: 1 })
  })
})

import { describe, expect, it } from 'vitest'
import type { ComfyAnalyzeEdge, ComfyBindingReading, ComfyNodeInfo } from '../../api/client'
import {
  assignInputRole,
  collapseGraph,
  edgeKey,
  fieldHighlight,
  fieldsForInput,
  fullGraph,
  inputUse,
  keptNodeIds,
  nodeCategory,
  mapEdges,
  nodePickable,
  nodeRoles,
  setInputExposed,
  toggleFinalPrompt,
  toggleOutput,
  withoutInputRef,
  type MapEdge,
  type MapGraph,
} from './bindingGraph'
import { layoutLayers } from './bindingMapLayout'
import { EMPTY_BINDINGS_FORM, type BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'

// -- 合成のグラフ(架空の値だけ)。状態と錨の種類は、サーバーの読み取りと同じ考え方の簡易版で付ける。 --

type Template = Record<string, { class_type: string; inputs: Record<string, unknown>; _meta?: { title: string } }>

const W = (id: string, port = 0) => [id, port]

function isWire(v: unknown): v is [string, number] {
  return Array.isArray(v) && v.length === 2 && typeof v[0] === 'string' && typeof v[1] === 'number'
}

function analyzeLike(template: Template): { nodes: ComfyNodeInfo[]; edges: ComfyAnalyzeEdge[] } {
  const edges: ComfyAnalyzeEdge[] = []
  for (const [id, node] of Object.entries(template)) {
    const sw = node.class_type === 'ComfySwitchNode' ? node.inputs.switch : undefined
    for (const [name, value] of Object.entries(node.inputs)) {
      if (!isWire(value)) continue
      const followed =
        typeof sw !== 'boolean' || !['on_true', 'on_false'].includes(name) || name === (sw ? 'on_true' : 'on_false')
      edges.push({ source: value[0], output: value[1], target: id, input: name, followed, output_type: null })
    }
  }
  const upstream = (starts: string[]) => {
    const seen = new Set<string>()
    const stack = [...starts]
    while (stack.length > 0) {
      const cur = stack.pop() as string
      if (seen.has(cur)) continue
      seen.add(cur)
      for (const e of edges) if (e.target === cur && e.followed) stack.push(e.source)
    }
    return seen
  }
  const ids = Object.keys(template)
  const cls = (id: string) => template[id].class_type
  const live = upstream(ids.filter((id) => /^Save/.test(cls(id))))
  const display = upstream(ids.filter((id) => /Preview/.test(cls(id)) && !live.has(id)))
  const kind = (id: string): ComfyNodeInfo['kind'] => {
    const c = cls(id)
    if (/^Save/.test(c)) return 'save'
    if (/KSampler|SamplerCustom/.test(c)) return 'sampler'
    if (/TextEncode/.test(c)) return 'encoder'
    if (c === 'ComfySwitchNode') return 'switch'
    if (/Preview/.test(c)) return 'display'
    if (c.startsWith('LoadImage')) return 'image_source'
    return null
  }
  const nodes: ComfyNodeInfo[] = ids.map((id) => ({
    id,
    class_type: cls(id),
    title: template[id]._meta?.title ?? cls(id),
    inputs: Object.entries(template[id].inputs).map(([name, value]) => ({ name, value, linked: isWire(value) })),
    state: live.has(id) ? 'live' : display.has(id) ? 'display' : 'unused',
    kind: kind(id),
  }))
  return { nodes, edges }
}

/** 公式の SD1.5 の t2i に近い構成(7 ノード)。 */
const SD15: Template = {
  '4': { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: 'model.safetensors' } },
  '6': { class_type: 'CLIPTextEncode', inputs: { text: 'a cat', clip: W('4', 1) } },
  '7': { class_type: 'CLIPTextEncode', inputs: { text: 'blurry', clip: W('4', 1) } },
  '5': { class_type: 'EmptyLatentImage', inputs: { width: 512, height: 512, batch_size: 1 } },
  '3': {
    class_type: 'KSampler',
    inputs: { seed: 1, steps: 20, cfg: 7, model: W('4'), positive: W('6'), negative: W('7'), latent_image: W('5') },
  },
  '8': { class_type: 'VAEDecode', inputs: { samples: W('3'), vae: W('4', 2) } },
  '9': { class_type: 'SaveImage', inputs: { images: W('8'), filename_prefix: 'ComfyUI' } },
}

/** 31 ノードの Edit(書式、切り替え、透過、表示だけの枝、使われないノード)。 */
const EDIT31: Template = {
  '3': { class_type: 'UNETLoader', inputs: { unet_name: 'edit.gguf', weight_dtype: 'default' } },
  '4': { class_type: 'CLIPLoader', inputs: { clip_name: 'te.safetensors', type: 'qwen_image' } },
  '5': { class_type: 'VAELoader', inputs: { vae_name: 'vae.safetensors' } },
  '6': { class_type: 'LoraLoaderModelOnly', inputs: { model: W('3'), lora_name: 'fast.safetensors', strength_model: 1 } },
  '7': { class_type: 'ModelSamplingAuraFlow', inputs: { model: W('6'), shift: 3.1 } },
  '8': { class_type: 'CFGNorm', inputs: { model: W('7'), strength: 1 } },
  '10': { class_type: 'LoadImage', inputs: { image: 'person.png' } },
  '11': { class_type: 'LoadImage', inputs: { image: 'jacket.png' } },
  '41': { class_type: 'LoadImage', inputs: { image: 'old.png' } },
  '459:470': { class_type: 'PrimitiveStringMultiline', inputs: { value: 'swap the jacket' }, _meta: { title: 'Instruction' } },
  '459:471': { class_type: 'PrimitiveStringMultiline', inputs: { value: 'photo style' }, _meta: { title: 'Style' } },
  '459:472': { class_type: 'StringFormat', inputs: { f_string: '{a}\n{b}', a: W('459:470'), b: W('459:471') } },
  '459:473': { class_type: 'PreviewAny', inputs: { source: W('459:472') } },
  '459:480': { class_type: 'ImageScaleToTotalPixels', inputs: { image: W('10'), megapixels: 1 } },
  '459:481': { class_type: 'ImageScaleToTotalPixels', inputs: { image: W('11'), megapixels: 1 } },
  '459:474': {
    class_type: 'TextEncodeQwenImageEditPlus',
    inputs: { clip: W('4'), vae: W('5'), prompt: W('459:472'), image1: W('459:480'), image2: W('459:481') },
  },
  '459:475': {
    class_type: 'TextEncodeQwenImageEditPlus',
    inputs: { clip: W('4'), vae: W('5'), prompt: '', image1: W('459:480'), image2: W('459:481') },
  },
  '459:477': { class_type: 'FluxKontextMultiReferenceLatentMethod', inputs: { conditioning: W('459:474') } },
  '459:478': { class_type: 'FluxKontextMultiReferenceLatentMethod', inputs: { conditioning: W('459:475') } },
  '459:483': { class_type: 'VAEEncode', inputs: { pixels: W('459:480'), vae: W('5') } },
  '459:490': { class_type: 'EmptySD3LatentImage', inputs: { width: 1024, height: 1024, batch_size: 1 } },
  '459:491': { class_type: 'ComfySwitchNode', inputs: { switch: false, on_false: W('459:483'), on_true: W('459:490') } },
  '459:500': {
    class_type: 'KSampler',
    inputs: { model: W('8'), positive: W('459:477'), negative: W('459:478'), latent_image: W('459:491'), seed: 8, steps: 4 },
  },
  '459:510': { class_type: 'VAEDecode', inputs: { samples: W('459:500'), vae: W('5') } },
  '20': { class_type: 'JoinImageWithAlpha', inputs: { image: W('459:510'), alpha: W('10', 1) } },
  '21': { class_type: 'SaveImage', inputs: { images: W('20'), filename_prefix: 'edit' } },
  '22': { class_type: 'PreviewImage', inputs: { images: W('459:510') } },
  '23': { class_type: 'ImageStitch', inputs: { image1: W('10'), image2: W('459:510') } },
  '24': { class_type: 'PreviewImage', inputs: { images: W('23') } },
  '459:495': { class_type: 'CLIPTextEncode', inputs: { clip: W('4'), text: 'old instruction' } },
  '459:496': { class_type: 'ConditioningZeroOut', inputs: { conditioning: W('459:495') } },
}

/** 100 ノード級: LoRA の鎖、ControlNet 3本、2回のアップスケール、たくさんの表示、使われない枝。 */
function big100(): Template {
  const g: Template = {}
  let next = 1
  const add = (class_type: string, inputs: Record<string, unknown>, title?: string) => {
    const id = String(next++)
    g[id] = { class_type, inputs, _meta: { title: title ?? class_type } }
    return id
  }
  const ckpt = add('CheckpointLoaderSimple', { ckpt_name: 'base.safetensors' })
  let model = ckpt
  let clip: unknown[] = W(ckpt, 1)
  for (let i = 0; i < 6; i += 1) {
    model = add('LoraLoader', { model: W(model), clip, lora_name: `l${i}.safetensors`, strength_model: 1 })
    clip = W(model, 1)
  }
  const vae = add('VAELoader', { vae_name: 'vae.safetensors' })
  const p1 = add('PrimitiveStringMultiline', { value: 'subject' }, 'Subject')
  const p2 = add('PrimitiveStringMultiline', { value: 'style' }, 'Style')
  const p3 = add('PrimitiveStringMultiline', { value: 'lighting' }, 'Lighting')
  const concat1 = add('StringConcatenate', { string_a: W(p1), string_b: W(p2) })
  const concat2 = add('StringConcatenate', { string_a: W(concat1), string_b: W(p3) })
  add('PreviewAny', { source: W(concat2) })
  let pos = add('CLIPTextEncode', { text: W(concat2), clip })
  const neg = add('CLIPTextEncode', { text: 'bad', clip })
  let negCond = neg
  for (let i = 0; i < 3; i += 1) {
    const loader = add('ControlNetLoader', { control_net_name: `cn${i}.safetensors` })
    const img = add('LoadImage', { image: `control${i}.png` })
    const scaled = add('ImageScale', { image: W(img), width: 1024, height: 1024 })
    const pre1 = add('CannyEdgePreprocessor', { image: W(scaled), low_threshold: 100 })
    const pre2 = add('ImageInvert', { image: W(pre1) })
    add('PreviewImage', { images: W(pre2) })
    const apply = add('ControlNetApplyAdvanced', {
      positive: W(pos),
      negative: W(negCond),
      control_net: W(loader),
      image: W(pre2),
      strength: 0.6,
    })
    pos = apply
    negCond = apply
  }
  const latent = add('EmptyLatentImage', { width: 1024, height: 1024, batch_size: 1 })
  const s1 = add('KSampler', {
    model: W(model),
    positive: W(pos),
    negative: W(negCond, 1),
    latent_image: W(latent),
    seed: 1,
    steps: 30,
  })
  let samples = s1
  let lastImage = ''
  for (let pass = 0; pass < 2; pass += 1) {
    const decoded = add('VAEDecode', { samples: W(samples), vae: W(vae) })
    add('PreviewImage', { images: W(decoded) })
    const upModel = add('UpscaleModelLoader', { model_name: `up${pass}.pth` })
    const up = add('ImageUpscaleWithModel', { upscale_model: W(upModel), image: W(decoded) })
    const down = add('ImageScaleBy', { image: W(up), scale_by: 0.5 })
    const sharpen = add('ImageSharpen', { image: W(down), sharpen_radius: 1 })
    const enc = add('VAEEncode', { pixels: W(sharpen), vae: W(vae) })
    const noise = add('LatentAddNoise', { samples: W(enc), amount: 0.1 })
    samples = add('KSampler', {
      model: W(model),
      positive: W(pos),
      negative: W(negCond, 1),
      latent_image: W(noise),
      seed: 2 + pass,
      steps: 20,
      denoise: 0.4,
    })
    lastImage = decoded
  }
  const final = add('VAEDecode', { samples: W(samples), vae: W(vae) })
  const color = add('ColorCorrect', { image: W(final), gamma: 1 })
  const grain = add('FilmGrain', { image: W(color), intensity: 0.1 })
  add('SaveImage', { images: W(grain), filename_prefix: 'big' })
  add('ImageCompare', { image_a: W(lastImage), image_b: W(grain) })
  for (let i = 0; i < 6; i += 1) {
    const prev = add('ImageBlur', { image: W(final), blur_radius: i })
    add('PreviewImage', { images: W(prev) })
  }
  // 使われない枝(どこにもつながらない古い試し)。
  let dead = add('LoadImage', { image: 'old.png' })
  for (let i = 0; i < 14; i += 1) dead = add(i % 2 === 0 ? 'ImageScale' : 'ImageInvert', { image: W(dead), width: 512 })
  // 足りない分は表示だけの枝で 100 にそろえる。
  while (next <= 100) add('PreviewImage', { images: W(final) })
  return g
}

function isAcyclic(graph: MapGraph): boolean {
  const succ = new Map<string, string[]>()
  for (const e of graph.boxEdges) succ.set(e.from, [...(succ.get(e.from) ?? []), e.to])
  const state = new Map<string, number>()
  const dfs = (id: string): boolean => {
    if (state.get(id) === 1) return false
    if (state.get(id) === 2) return true
    state.set(id, 1)
    for (const n of succ.get(id) ?? []) if (!dfs(n)) return false
    state.set(id, 2)
    return true
  }
  return graph.boxes.every((b) => dfs(b.id))
}

function collapsed(template: Template, bindings: BindingsFormState = EMPTY_BINDINGS_FORM, rows: ExposedParamRow[] = []) {
  const { nodes, edges } = analyzeLike(template)
  const mapped = mapEdges(nodes, edges)
  const keep = keptNodeIds(nodes, nodeRoles(bindings, rows))
  return { nodes, edges: mapped, keep, graph: collapseGraph(nodes, mapped, keep) }
}

const EDIT31_BINDINGS: BindingsFormState = {
  ...EMPTY_BINDINGS_FORM,
  prompt: { node: '459:470', input: 'value' },
  negativePrompt: { node: '459:475', input: 'prompt' },
  seeds: [{ node: '459:500', input: 'seed' }],
  images: [
    { node: '10', input: 'image' },
    { node: '11', input: 'image' },
  ],
  outputs: ['21'],
  finalPrompt: '459:473',
}

function row(node: string, input: string, enabled: boolean, name = input): ExposedParamRow {
  return {
    enabled,
    name,
    node,
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

describe('nodeCategory', () => {
  it('クラス名から大まかな種類を推す', () => {
    expect(nodeCategory({ class_type: 'LoraLoaderModelOnly' })).toBe('model')
    expect(nodeCategory({ class_type: 'StringFormat' })).toBe('text')
    expect(nodeCategory({ class_type: 'PrimitiveStringMultiline' })).toBe('text')
    expect(nodeCategory({ class_type: 'FluxKontextMultiReferenceLatentMethod' })).toBe('cond')
    expect(nodeCategory({ class_type: 'ImageScaleToTotalPixels' })).toBe('image')
    expect(nodeCategory({ class_type: 'SomethingCustom' })).toBe('other')
  })
})

describe('collapseGraph', () => {
  it('公式風の小さな構成は、錨を残してほとんど束ねない', () => {
    const { graph } = collapsed(SD15)
    expect(graph.boxes.length).toBeLessThanOrEqual(7)
    const single = new Set(graph.boxes.filter((b) => b.nodeIds.length === 1).map((b) => b.nodeIds[0]))
    for (const id of ['3', '6', '7', '9']) expect(single.has(id)).toBe(true)
    expect(isAcyclic(graph)).toBe(true)
  })

  it('31 ノードの Edit は 10〜20 個の箱に収まり、役割と錨は1つずつ残る', () => {
    const { graph, nodes } = collapsed(EDIT31, EDIT31_BINDINGS, [row('459:500', 'steps', true)])
    expect(graph.boxes.length).toBeGreaterThanOrEqual(10)
    expect(graph.boxes.length).toBeLessThanOrEqual(20)
    const singles = new Set(graph.boxes.filter((b) => b.nodeIds.length === 1).map((b) => b.nodeIds[0]))
    for (const id of ['459:470', '459:475', '459:500', '10', '11', '21', '459:473', '459:474', '459:491']) {
      expect(singles.has(id)).toBe(true)
    }
    // すべてのノードがちょうど1つの箱に入る。
    const all = graph.boxes.flatMap((b) => b.nodeIds).sort()
    expect(all).toEqual(nodes.map((n) => n.id).sort())
    expect(isAcyclic(graph)).toBe(true)
    // 使われないノード(41、459:495、459:496、459:490)は1つの束。
    const unused = graph.boxes.filter((b) => b.state === 'unused')
    expect(unused).toHaveLength(1)
    expect(unused[0].nodeIds.sort()).toEqual(['41', '459:490', '459:495', '459:496'])
    // 表示だけのノード(22、23、24)も1つの束。
    const display = graph.boxes.filter((b) => b.state === 'display' && !b.kept)
    expect(display).toHaveLength(1)
    expect(display[0].nodeIds.sort()).toEqual(['22', '23', '24'])
    // モデルの読み込みの鎖は1つの束。
    const model = graph.boxes.find((b) => b.nodeIds.includes('3'))
    expect(model?.nodeIds).toEqual(expect.arrayContaining(['3', '6', '7', '8']))
  })

  it('100 ノード級でも 10〜20 個の箱に収まり、循環しない', () => {
    const template = big100()
    expect(Object.keys(template)).toHaveLength(100)
    const { graph } = collapsed(template)
    expect(graph.boxes.length).toBeGreaterThanOrEqual(10)
    expect(graph.boxes.length).toBeLessThanOrEqual(20)
    expect(isAcyclic(graph)).toBe(true)
    // サンプラー3つと保存は残る。
    const keptCls = graph.boxes.filter((b) => b.kept).length
    expect(keptCls).toBeGreaterThanOrEqual(4)
  })

  it('すべてのノードの表示は1ノード1箱で、配線を箱の間に写す', () => {
    const { nodes, edges } = analyzeLike(EDIT31)
    const graph = fullGraph(nodes, mapEdges(nodes, edges))
    expect(graph.boxes).toHaveLength(31)
    const sw = graph.boxEdges.find((e) => e.from === '459:490' && e.to === '459:491')
    expect(sw?.state).toBe('unused')
    const live = graph.boxEdges.find((e) => e.from === '459:483' && e.to === '459:491')
    expect(live?.state).toBe('live')
  })
})

describe('layoutLayers', () => {
  it('配線は左から右へ流れ、同じ段の箱は重ならない', () => {
    for (const template of [SD15, EDIT31, big100()]) {
      const { graph } = collapsed(template)
      const boxes = graph.boxes.map((b) => ({ id: b.id, width: 180, height: 40 + b.nodeIds.length, sink: b.state === 'unused' }))
      const result = layoutLayers(boxes, graph.boxEdges)
      for (const e of graph.boxEdges) {
        expect(result.positions.get(e.from)!.x).toBeLessThan(result.positions.get(e.to)!.x)
      }
      const byX = new Map<number, { y: number; h: number }[]>()
      for (const b of boxes) {
        const p = result.positions.get(b.id)!
        byX.set(p.x, [...(byX.get(p.x) ?? []), { y: p.y, h: b.height }])
      }
      for (const col of byX.values()) {
        col.sort((a, b) => a.y - b.y)
        for (let i = 1; i < col.length; i += 1) expect(col[i].y).toBeGreaterThanOrEqual(col[i - 1].y + col[i - 1].h)
      }
      expect(result.width).toBeGreaterThan(0)
      expect(Math.min(...[...result.positions.values()].map((p) => p.y))).toBe(16)
    }
  })

  it('上 → 下の向きでは、配線は上から下へ流れ、同じ段の箱は横に重ならない', () => {
    for (const template of [SD15, EDIT31, big100()]) {
      const { graph } = collapsed(template)
      const boxes = graph.boxes.map((b) => ({ id: b.id, width: 180, height: 40 + b.nodeIds.length, sink: b.state === 'unused' }))
      const lr = layoutLayers(boxes, graph.boxEdges)
      const tb = layoutLayers(boxes, graph.boxEdges, { direction: 'TB', columnGap: 40, rowGap: 14 })
      for (const e of graph.boxEdges) {
        expect(tb.positions.get(e.from)!.y).toBeLessThan(tb.positions.get(e.to)!.y)
      }
      // 段は向きによらず同じ。
      expect([...tb.layers]).toEqual([...lr.layers])
      const byY = new Map<number, { x: number; w: number }[]>()
      for (const b of boxes) {
        const p = tb.positions.get(b.id)!
        byY.set(p.y, [...(byY.get(p.y) ?? []), { x: p.x, w: b.width }])
      }
      for (const row of byY.values()) {
        row.sort((a, b) => a.x - b.x)
        for (let i = 1; i < row.length; i += 1) expect(row[i].x).toBeGreaterThanOrEqual(row[i - 1].x + row[i - 1].w)
      }
      // 図の大きさは、箱がすべて入る大きさ。
      for (const b of boxes) {
        const p = tb.positions.get(b.id)!
        expect(p.x + b.width).toBeLessThanOrEqual(tb.width)
        expect(p.y + b.height).toBeLessThanOrEqual(tb.height)
      }
    }
  })

  it('背骨(主要な道筋)の箱は一直線に並び、段の先頭に来る', () => {
    const boxes = ['a', 'b', 'c', 'x', 'y', 'z'].map((id) => ({ id, width: 100, height: 40 }))
    const edges = [
      { from: 'x', to: 'b' },
      { from: 'y', to: 'b' },
      { from: 'a', to: 'b' },
      { from: 'b', to: 'c' },
      { from: 'z', to: 'c' },
    ]
    for (const direction of ['LR', 'TB'] as const) {
      const r = layoutLayers(boxes, edges, { direction, spine: ['a', 'b', 'c'] })
      const cross = (id: string) => (direction === 'LR' ? r.positions.get(id)!.y : r.positions.get(id)!.x)
      expect(cross('a')).toBe(cross('b'))
      expect(cross('b')).toBe(cross('c'))
      // 背骨は段の先頭(いちばん上、または左)。
      expect(cross('a')).toBeLessThan(cross('x'))
      expect(cross('a')).toBeLessThan(cross('y'))
    }
  })

  it('読み込みのノードは使う側の1つ前の段へ寄せる', () => {
    const result = layoutLayers(
      [
        { id: 'a', width: 10, height: 10 },
        { id: 'b', width: 10, height: 10 },
        { id: 'c', width: 10, height: 10 },
        { id: 'loader', width: 10, height: 10 },
      ],
      [
        { from: 'a', to: 'b' },
        { from: 'b', to: 'c' },
        { from: 'loader', to: 'c' },
      ],
    )
    expect(result.layers.get('loader')).toBe(1)
    expect(result.layers.get('c')).toBe(2)
  })

  it('空でも落ちない', () => {
    expect(layoutLayers([], []).width).toBe(0)
  })
})

describe('roles and assignment', () => {
  const { nodes } = analyzeLike(EDIT31)
  const node = (id: string) => nodes.find((n) => n.id === id)!
  const input = (id: string, name: string) => node(id).inputs!.find((i) => i.name === name)!

  it('ノードごとの役割の札(枠の番号、出力、公開)', () => {
    const roles = nodeRoles(EDIT31_BINDINGS, [row('459:500', 'steps', true), row('459:500', 'cfg', false)])
    expect(roles.get('11')).toEqual([{ role: 'image', input: 'image', slot: 2 }])
    expect(roles.get('21')).toEqual([{ role: 'output', input: null }])
    expect(roles.get('459:500')).toEqual([
      { role: 'seed', input: 'seed' },
      { role: 'exposed', input: 'steps' },
    ])
  })

  it('入力の扱い: 配線、差し込む、公開、固定', () => {
    const roles = nodeRoles(EDIT31_BINDINGS, [row('459:500', 'steps', true)])
    expect(inputUse(node('459:500'), input('459:500', 'model'), roles)).toEqual({ kind: 'wired' })
    expect(inputUse(node('459:500'), input('459:500', 'seed'), roles)).toEqual({ kind: 'bound', fields: ['seed'] })
    expect(inputUse(node('459:500'), input('459:500', 'steps'), roles)).toEqual({ kind: 'exposed' })
    expect(inputUse(node('459:490'), input('459:490', 'width'), roles)).toEqual({ kind: 'fixed' })
  })

  it('割り当てられる項目は値の種類で決まり、配線された入力には無い', () => {
    // 入力名の合う項目だけ(幅の入力をシードにはしない)。
    expect(fieldsForInput(node('459:490'), input('459:490', 'width'), 'edit')).toEqual(['width'])
    expect(fieldsForInput(node('459:500'), input('459:500', 'steps'), 'edit')).toEqual([])
    // 文字列でも、選択肢のような入力(sampler_name など)はプロンプトにしない。
    expect(fieldsForInput(node('459:472'), input('459:472', 'f_string'), 'edit')).toEqual([])
    // ファイル名の文字列はプロンプトにしない。
    expect(fieldsForInput(node('10'), input('10', 'image'), 'edit')).toEqual(['images', 'mask'])
    expect(fieldsForInput(node('10'), input('10', 'image'), 'generate')).toEqual([])
    expect(fieldsForInput(node('459:470'), input('459:470', 'value'), 'generate')).toEqual(['prompt', 'negative_prompt'])
    expect(fieldsForInput(node('459:472'), input('459:472', 'a'), 'edit')).toEqual([])
    expect(nodePickable(node('21'), 'outputs')).toBe(true)
    expect(nodePickable(node('459:473'), 'outputs')).toBe(false)
    expect(nodePickable(node('459:473'), 'final_prompt')).toBe(true)
    expect(nodePickable(node('459:500'), 'seed')).toBe(true)
    expect(nodePickable(node('5'), 'seed')).toBe(false)
  })

  it('差し込む: ほかの項目からは外し、枠とシードは末尾に足す', () => {
    const ref = { node: '459:471', input: 'value' }
    const asNegative = assignInputRole(EDIT31_BINDINGS, 'negative_prompt', ref)
    expect(asNegative.negativePrompt).toEqual(ref)
    const asPrompt = assignInputRole(asNegative, 'prompt', ref)
    expect(asPrompt.prompt).toEqual(ref)
    expect(asPrompt.negativePrompt).toBeNull()
    const slot = assignInputRole(EDIT31_BINDINGS, 'images', { node: '41', input: 'image' })
    expect(slot.images.map((r) => r.node)).toEqual(['10', '11', '41'])
    const mask = assignInputRole(slot, 'mask', { node: '41', input: 'image' })
    expect(mask.images.map((r) => r.node)).toEqual(['10', '11'])
    expect(mask.maskMode).toBe('load_image_mask')
    expect(withoutInputRef(mask, { node: '41', input: 'image' }).maskMode).toBe('none')
    expect(toggleOutput(EDIT31_BINDINGS, '21').outputs).toEqual([])
    expect(toggleOutput(EDIT31_BINDINGS, '22').outputs).toEqual(['21', '22'])
    expect(toggleFinalPrompt(EDIT31_BINDINGS, '459:473').finalPrompt).toBeNull()
  })

  it('公開: 候補の行があればチェックを付け、無ければ重ならない名前で足す', () => {
    const rows = [row('459:500', 'steps', false), row('459:490', 'width', false, 'width')]
    expect(setInputExposed(rows, node('459:500'), input('459:500', 'steps'), true)[0].enabled).toBe(true)
    const added = setInputExposed(rows, node('20'), { name: 'width', value: 3, linked: false }, true)
    expect(added).toHaveLength(3)
    expect(added[2]).toMatchObject({ enabled: true, name: 'width_20', node: '20', type: 'int', default: 3 })
    const seedRow = setInputExposed([], node('459:500'), input('459:500', 'seed'), true)
    // 予約語(seed)は使わない。
    expect(seedRow[0].name).toBe('seed_459_500')
    expect(setInputExposed(rows, node('20'), { name: 'x', value: 1.5, linked: false }, false)).toHaveLength(2)
  })
})

describe('fieldHighlight', () => {
  const { nodes, edges } = analyzeLike(EDIT31)
  const mapped: MapEdge[] = mapEdges(nodes, edges)
  const keys = (list: [string, string, string][]) => list.map(([source, target, input]) => edgeKey({ source, target, input }))

  it('プロンプトは出力までの道筋を金で示す(選ばれなかった枝は通らない)', () => {
    const h = fieldHighlight('prompt', EDIT31_BINDINGS, nodes, mapped, undefined)
    expect([...h.pathNodes]).toEqual(['459:470', '459:472', '459:474', '459:477', '459:500', '459:510', '20', '21'])
    expect([...h.pathEdges]).toContain(keys([['459:472', '459:474', 'prompt']])[0])
  })

  it('画像の枠は IMAGE の出力からの道筋(透過のための MASK の近道は通らない)', () => {
    const h = fieldHighlight('images', EDIT31_BINDINGS, nodes, mapped, undefined)
    expect(h.pathEdges.has(keys([['10', '20', 'alpha']])[0])).toBe(false)
    expect([...h.pathNodes]).toEqual(expect.arrayContaining(['10', '459:480', '459:500', '21']))
  })

  it('最終プロンプトは表示している配線を示す', () => {
    const h = fieldHighlight('final_prompt', EDIT31_BINDINGS, nodes, mapped, undefined)
    expect([...h.pathNodes].sort()).toEqual(['459:472', '459:473'])
  })

  it('差し込まない項目は根拠のノードと配線を示す', () => {
    const reading: ComfyBindingReading = {
      field: 'width',
      confidence: 'absent',
      reason: 'from image',
      paths: [],
      candidates: [],
      evidence: [
        { node: '459:483', class_type: 'VAEEncode', input: null },
        { node: '459:491', class_type: 'ComfySwitchNode', input: 'on_true' },
        { node: '459:490', class_type: 'EmptySD3LatentImage', input: null },
      ],
    }
    const h = fieldHighlight('width', EDIT31_BINDINGS, nodes, mapped, reading)
    expect(h.pathNodes.size).toBe(0)
    expect([...h.evidenceNodes].sort()).toEqual(['459:483', '459:490', '459:491'])
    expect([...h.evidenceEdges].sort()).toEqual(
      keys([
        ['459:483', '459:491', 'on_false'],
        ['459:490', '459:491', 'on_true'],
      ]).sort(),
    )
  })

  it('配線が無くてもノードの入力から図を作れる', () => {
    const fallback = mapEdges(nodes, null)
    expect(fallback).toHaveLength(mapped.length)
    expect(fallback.every((e) => e.followed)).toBe(true)
  })
})

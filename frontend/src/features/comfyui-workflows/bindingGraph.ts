/**
 * 差し込み先の図(ADR-0052 4章)の中身を計算する純粋関数。描画(`BindingMap.tsx`)とは分ける。
 *
 * - 配線: analyze の `edges`(切り替えで選ばれなかった枝は `followed=false`)。無ければノードの入力から作る。
 * - 役割: 画面の値(差し込み先 `BindingsFormState` と公開パラメーターの行)から、ノードと入力ごとの役割を引く。
 * - 束ね方(`collapseGraph`): 役割を持つノードと錨(サンプラー、エンコーダー、保存、切り替え)を残し、
 *   それ以外を、配線でつながる鎖・同じ段の兄弟・同じ状態の残りの順で束ねる。束ねても図が循環しないものだけ。
 * - 強調(`fieldHighlight`): 項目の値から出力までの道筋、差し込まない項目の根拠。
 * - 図からの割り当て(`assignInputRole` ほか): 結果は第2段と同じ `BindingsFormState` と公開パラメーターの行。
 *   テンプレートの値は変えない。
 */
import type {
  ComfyAnalyzeEdge,
  ComfyBindingReading,
  ComfyNodeInfo,
  ComfyNodeInputInfo,
  ComfyParamType,
} from '../../api/client'
import type { FieldKey } from './bindingCards'
import type { BindingsFormState } from './bindingsForm'
import { validateExposedParamName, type ExposedParamRow } from './exposedParamsForm'
import type { InputRefValue } from './nodeOptions'

export type NodeState = ComfyNodeInfo['state']

const STATE_RANK: Record<NodeState, number> = { live: 2, display: 1, unused: 0 }

/** 2つの状態のうち強い方(出力につながる > 表示だけ > 使われない)。 */
export function strongerState(a: NodeState, b: NodeState): NodeState {
  return STATE_RANK[a] >= STATE_RANK[b] ? a : b
}

// -- 配線 ----------------------------------------------------------------------

export interface MapEdge {
  source: string
  output: number
  target: string
  input: string
  /** 切り替えで選ばれなかった枝なら false。 */
  followed: boolean
  outputType: string | null
}

function isWireValue(value: unknown): value is [string, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === 'string' &&
    typeof value[1] === 'number' &&
    Number.isInteger(value[1])
  )
}

/**
 * 図の配線。analyze の `edges` があればそれを使い、無ければ(読み取りに失敗した編集画面など)
 * ノードの入力の配線から作る(このときは切り替えを解かず、すべて辿る)。
 */
export function mapEdges(
  nodes: readonly ComfyNodeInfo[],
  edges: readonly ComfyAnalyzeEdge[] | null | undefined,
): MapEdge[] {
  const ids = new Set(nodes.map((n) => n.id))
  if (edges && edges.length > 0) {
    return edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({
        source: e.source,
        output: e.output,
        target: e.target,
        input: e.input,
        followed: e.followed ?? true,
        outputType: e.output_type ?? null,
      }))
  }
  const result: MapEdge[] = []
  for (const node of nodes) {
    for (const input of node.inputs ?? []) {
      if (!input.linked || !isWireValue(input.value) || !ids.has(input.value[0])) continue
      result.push({
        source: input.value[0],
        output: input.value[1],
        target: node.id,
        input: input.name,
        followed: true,
        outputType: null,
      })
    }
  }
  return result
}

/** 配線の状態。選ばれなかった枝と、使われないノードに触れる配線は「使われない」。 */
export function edgeState(edge: MapEdge, states: ReadonlyMap<string, NodeState>): NodeState {
  if (!edge.followed) return 'unused'
  const a = states.get(edge.source) ?? 'unused'
  const b = states.get(edge.target) ?? 'unused'
  if (a === 'unused' || b === 'unused') return 'unused'
  if (a === 'display' || b === 'display') return 'display'
  return 'live'
}

export function edgeKey(edge: Pick<MapEdge, 'source' | 'target' | 'input'>): string {
  return `${edge.source}\u0000${edge.target}\u0000${edge.input}`
}

// -- 役割 ----------------------------------------------------------------------

/** ノードに付ける役割の札。`image` は枠(`slot` が 1 始まりの番号)、`exposed` は公開パラメーター。 */
export type MapRole =
  | 'prompt'
  | 'negative_prompt'
  | 'seed'
  | 'width'
  | 'height'
  | 'batch_size'
  | 'image'
  | 'mask'
  | 'output'
  | 'final_prompt'
  | 'exposed'

export interface RoleBadge {
  role: MapRole
  /** 入力名(ノードそのものの役割では null)。 */
  input: string | null
  /** 画像の枠の番号(1 始まり)。 */
  slot?: number
}

function setRef(ref: InputRefValue | null | undefined): ref is InputRefValue {
  return !!ref && ref.node !== '' && ref.input !== ''
}

/** ノードごとの役割の札(差し込み先、出力、最終プロンプト、公開パラメーター)。 */
export function nodeRoles(
  bindings: BindingsFormState,
  exposedRows: readonly ExposedParamRow[],
): Map<string, RoleBadge[]> {
  const map = new Map<string, RoleBadge[]>()
  const push = (node: string, badge: RoleBadge) => {
    const list = map.get(node) ?? []
    list.push(badge)
    map.set(node, list)
  }
  const single: [MapRole, InputRefValue | null][] = [
    ['prompt', bindings.prompt],
    ['negative_prompt', bindings.negativePrompt],
    ['width', bindings.width],
    ['height', bindings.height],
    ['batch_size', bindings.batchSize],
  ]
  for (const [role, ref] of single) if (setRef(ref)) push(ref.node, { role, input: ref.input })
  for (const ref of bindings.seeds) if (setRef(ref)) push(ref.node, { role: 'seed', input: ref.input })
  bindings.images.filter(setRef).forEach((ref, i) => push(ref.node, { role: 'image', input: ref.input, slot: i + 1 }))
  if (bindings.maskMode === 'load_image_mask' && setRef(bindings.maskRef)) {
    push(bindings.maskRef.node, { role: 'mask', input: bindings.maskRef.input })
  }
  for (const id of bindings.outputs) push(id, { role: 'output', input: null })
  if (bindings.finalPrompt) push(bindings.finalPrompt, { role: 'final_prompt', input: null })
  for (const row of exposedRows) if (row.enabled) push(row.node, { role: 'exposed', input: row.input })
  return map
}

/** 入力の扱い: 配線 / 差し込む(役割)/ 公開 / 固定(テンプレートの値のまま)。 */
export type InputUse =
  | { kind: 'wired' }
  | { kind: 'bound'; fields: FieldKey[] }
  | { kind: 'exposed' }
  | { kind: 'fixed' }

const ROLE_FIELD: Partial<Record<MapRole, FieldKey>> = {
  prompt: 'prompt',
  negative_prompt: 'negative_prompt',
  seed: 'seed',
  width: 'width',
  height: 'height',
  batch_size: 'batch_size',
  image: 'images',
  mask: 'mask',
}

export function inputUse(
  node: ComfyNodeInfo,
  input: ComfyNodeInputInfo,
  roles: ReadonlyMap<string, readonly RoleBadge[]>,
): InputUse {
  if (input.linked) return { kind: 'wired' }
  const badges = (roles.get(node.id) ?? []).filter((b) => b.input === input.name)
  const fields = badges.map((b) => ROLE_FIELD[b.role]).filter((f): f is FieldKey => f !== undefined)
  if (fields.length > 0) return { kind: 'bound', fields: [...new Set(fields)] }
  if (badges.some((b) => b.role === 'exposed')) return { kind: 'exposed' }
  return { kind: 'fixed' }
}

// -- 図で選べる入力 ----------------------------------------------------------------

/** 入力を1つ選ぶ項目(ノードそのものを選ぶ出力・最終プロンプト以外)。 */
export type InputField = Exclude<FieldKey, 'outputs' | 'final_prompt'>

export const INPUT_FIELDS: readonly InputField[] = [
  'prompt',
  'negative_prompt',
  'images',
  'mask',
  'seed',
  'width',
  'height',
  'batch_size',
]

const FILE_VALUE_RE = /\.(png|jpe?g|webp|gif|bmp|safetensors|ckpt|pt|pth|bin|gguf|onnx)$/i

/** 文字列の差し込み先になりうる入力名(エンコーダーの文、Primitive の値など)。 */
const TEXT_INPUT_RE = /^(text|text_[gl]|prompt|negative_prompt|positive|negative|value|string|clip_l|t5xxl)$/i

/** 数の差し込み先になりうる入力名(項目ごと)。Primitive の `value` はどれにもなりうる。 */
const NUMBER_INPUT_RE: Record<'seed' | 'width' | 'height' | 'batch_size', RegExp> = {
  seed: /seed/i,
  width: /width/i,
  height: /height/i,
  batch_size: /^(batch_size|batch|amount|length)$/i,
}

/**
 * 入力がその項目の差し込み先の候補になるか(値の種類と入力名。画像は読み込みのノード)。
 * 名前の合わない入力(`steps` を幅にする、など)は出さない。正しいかの最終的な判断は保存時の検証
 * (サーバー)が持つ。
 */
export function inputFitsField(node: ComfyNodeInfo, input: ComfyNodeInputInfo, field: InputField): boolean {
  if (input.linked) return false
  const value = input.value
  const cls = node.class_type ?? ''
  const primitive = cls.startsWith('Primitive') && input.name === 'value'
  switch (field) {
    case 'prompt':
    case 'negative_prompt':
      return typeof value === 'string' && !FILE_VALUE_RE.test(value) && (primitive || TEXT_INPUT_RE.test(input.name))
    case 'seed':
    case 'width':
    case 'height':
    case 'batch_size':
      return (
        typeof value === 'number' &&
        Number.isInteger(value) &&
        (primitive || NUMBER_INPUT_RE[field].test(input.name))
      )
    case 'images':
    case 'mask':
      return cls.startsWith('LoadImage') && input.name === 'image' && typeof value === 'string'
  }
}

/** その入力に割り当てられる項目(並びは画面の順)。 */
export function fieldsForInput(
  node: ComfyNodeInfo,
  input: ComfyNodeInputInfo,
  operation: 'generate' | 'edit',
): InputField[] {
  return INPUT_FIELDS.filter(
    (field) => (operation === 'edit' || (field !== 'images' && field !== 'mask')) && inputFitsField(node, input, field),
  )
}

const TEXT_DISPLAY_RE = /PreviewAny|ShowText|PreviewText|DisplayText/i

/** ノードそのものを出力にできるか(保存ノード、ほかに画像を表示するノード)。 */
export function nodeFitsOutput(node: ComfyNodeInfo): boolean {
  const cls = node.class_type ?? ''
  if (TEXT_DISPLAY_RE.test(cls)) return false
  return node.kind === 'save' || node.kind === 'display' || /Save|Preview/.test(cls)
}

/** ノードそのものを最終プロンプトにできるか(文字列を表示するノード)。 */
export function nodeFitsFinalPrompt(node: ComfyNodeInfo): boolean {
  return TEXT_DISPLAY_RE.test(node.class_type ?? '')
}

/** 「図で選ぶ」で選べるノードか。 */
export function nodePickable(node: ComfyNodeInfo, field: FieldKey): boolean {
  if (field === 'outputs') return nodeFitsOutput(node)
  if (field === 'final_prompt') return nodeFitsFinalPrompt(node)
  return (node.inputs ?? []).some((input) => inputFitsField(node, input, field))
}

// -- 割り当て ----------------------------------------------------------------------

function sameRef(a: InputRefValue | null | undefined, b: InputRefValue): boolean {
  return !!a && a.node === b.node && a.input === b.input
}

/** その入力をどの差し込み先からも外す。 */
export function withoutInputRef(bindings: BindingsFormState, ref: InputRefValue): BindingsFormState {
  const maskHit = bindings.maskMode === 'load_image_mask' && sameRef(bindings.maskRef, ref)
  return {
    ...bindings,
    prompt: sameRef(bindings.prompt, ref) ? null : bindings.prompt,
    negativePrompt: sameRef(bindings.negativePrompt, ref) ? null : bindings.negativePrompt,
    width: sameRef(bindings.width, ref) ? null : bindings.width,
    height: sameRef(bindings.height, ref) ? null : bindings.height,
    batchSize: sameRef(bindings.batchSize, ref) ? null : bindings.batchSize,
    seeds: bindings.seeds.filter((r) => !sameRef(r, ref)),
    images: bindings.images.filter((r) => !sameRef(r, ref)),
    maskMode: maskHit ? 'none' : bindings.maskMode,
    maskRef: maskHit ? null : bindings.maskRef,
  }
}

/**
 * 入力を項目の差し込み先にする。ほかの項目からは外す。1つだけの項目は置き換え、シードと画像の枠は
 * 末尾に足す(枠の番号は今の枠の数 + 1)。マスクは「別ファイルのマスク」の方式にする。
 */
export function assignInputRole(
  bindings: BindingsFormState,
  field: InputField,
  ref: InputRefValue,
): BindingsFormState {
  const b = withoutInputRef(bindings, ref)
  const filled = (refs: InputRefValue[]) => refs.filter((r) => r.node !== '' && r.input !== '')
  switch (field) {
    case 'prompt':
      return { ...b, prompt: ref }
    case 'negative_prompt':
      return { ...b, negativePrompt: ref }
    case 'width':
      return { ...b, width: ref }
    case 'height':
      return { ...b, height: ref }
    case 'batch_size':
      return { ...b, batchSize: ref }
    case 'seed':
      return { ...b, seeds: [...filled(b.seeds), ref] }
    case 'images':
      return { ...b, images: [...filled(b.images), ref] }
    case 'mask':
      return { ...b, maskMode: 'load_image_mask', maskRef: ref }
  }
}

/** ノードを出力に足す・外す。 */
export function toggleOutput(bindings: BindingsFormState, nodeId: string): BindingsFormState {
  const outputs = bindings.outputs.includes(nodeId)
    ? bindings.outputs.filter((id) => id !== nodeId)
    : [...bindings.outputs, nodeId]
  return { ...bindings, outputs }
}

/** ノードを最終プロンプトにする・外す。 */
export function toggleFinalPrompt(bindings: BindingsFormState, nodeId: string): BindingsFormState {
  return { ...bindings, finalPrompt: bindings.finalPrompt === nodeId ? null : nodeId }
}

function inferParamType(value: unknown): ComfyParamType | null {
  if (typeof value === 'boolean') return 'bool'
  if (typeof value === 'number') return Number.isInteger(value) ? 'int' : 'float'
  if (typeof value === 'string') return 'text'
  return null
}

function sanitizeParamName(name: string): string {
  let s = name.toLowerCase().replace(/[^a-z0-9_]/g, '_')
  if (s === '' || !/^[a-z]/.test(s)) s = s ? `p_${s}` : 'p'
  return s
}

/** 入力を公開する・しない。公開パラメーターの候補に無い入力は、行を作って足す(名前は重ならないように)。 */
export function setInputExposed(
  rows: readonly ExposedParamRow[],
  node: ComfyNodeInfo,
  input: ComfyNodeInputInfo,
  enabled: boolean,
): ExposedParamRow[] {
  const index = rows.findIndex((row) => row.node === node.id && row.input === input.name)
  if (index >= 0) return rows.map((row, i) => (i === index ? { ...row, enabled } : row))
  if (!enabled) return [...rows]
  const type = inferParamType(input.value)
  if (type === null) return [...rows]
  const taken = new Set(rows.map((row) => row.name))
  const candidates = [sanitizeParamName(input.name), sanitizeParamName(`${input.name}_${node.id}`)]
  let name = candidates.find((n) => !taken.has(n) && validateExposedParamName(n) === null)
  for (let i = 2; name === undefined; i += 1) {
    const n = sanitizeParamName(`${input.name}_${i}`)
    if (!taken.has(n) && validateExposedParamName(n) === null) name = n
  }
  return [
    ...rows,
    {
      enabled: true,
      name,
      node: node.id,
      input: input.name,
      type,
      label: input.name,
      default: input.value ?? null,
      minimum: null,
      maximum: null,
      step: null,
      choices: null,
      maxLength: null,
    },
  ]
}

// -- 束ね方 ----------------------------------------------------------------------

export interface MapBox {
  /** 単独のノードはノードの id、束は `group:` で始まる id。 */
  id: string
  nodeIds: string[]
  state: NodeState
  /** 役割か錨で残したノード(束ねない)。 */
  kept: boolean
}

export interface MapBoxEdge {
  id: string
  from: string
  to: string
  state: NodeState
  edges: MapEdge[]
}

export interface MapGraph {
  boxes: MapBox[]
  boxEdges: MapBoxEdge[]
  /** ノードの id → 箱の id。 */
  boxOf: Map<string, string>
}

/**
 * 束ねるときの大まかな種類(クラス名から推す)。種類の違う箱は、鎖でつながっていても束ねない
 * (「文字列の整形」と「画像の縮小」が1つの箱にならないように)。`other` はどれとも束ねられる。
 */
export type NodeCategory = 'model' | 'text' | 'image' | 'cond' | 'other'

export function nodeCategory(node: Pick<ComfyNodeInfo, 'class_type'>): NodeCategory {
  const cls = node.class_type ?? ''
  if (/Loader|^Load(?!Image)|^Unet|ModelSampling|CFGNorm|^Lora|ModelMerge|PatchModel|Hook/i.test(cls)) return 'model'
  if (/Conditioning|ControlNet|Guidance|Guider|ReferenceLatent|Style(Model)?Apply|unCLIP/i.test(cls)) return 'cond'
  // 大文字小文字を区別する(`FluxKontext` の `text` を文字列と取り違えないように)。
  if (/String|Text(?!Encode)|Prompt/.test(cls)) return 'text'
  if (/Image|Latent|Pixel|VAE|Upscale|Mask|Preprocess|Canny|Depth|Pose|Sharpen|Blur|Invert|Color|Grain|Stitch|Compare|Crop|Pad|Resize/i.test(cls))
    return 'image'
  return 'other'
}

/** 錨として残すノードの種類。 */
const ANCHOR_KINDS = new Set(['save', 'sampler', 'encoder', 'switch'])

/**
 * 縮約した図で残すノード: 役割を持つノード、錨(保存・サンプラー・エンコーダー・切り替え)、
 * 読み取りの候補と根拠のノード。使われないノードは役割が無ければ残さない。
 */
export function keptNodeIds(
  nodes: readonly ComfyNodeInfo[],
  roles: ReadonlyMap<string, readonly RoleBadge[]>,
  readings: readonly ComfyBindingReading[] = [],
): Set<string> {
  const keep = new Set<string>()
  const ids = new Set(nodes.map((n) => n.id))
  for (const node of nodes) {
    if (roles.has(node.id)) keep.add(node.id)
    else if (node.kind && ANCHOR_KINDS.has(node.kind) && node.state !== 'unused') keep.add(node.id)
  }
  for (const reading of readings) {
    for (const candidate of reading.candidates ?? []) if (ids.has(candidate.node)) keep.add(candidate.node)
  }
  return keep
}

/** すべてのノードを1つずつの箱にする。 */
export function fullGraph(nodes: readonly ComfyNodeInfo[], edges: readonly MapEdge[]): MapGraph {
  const boxes = nodes.map((n) => ({ id: n.id, nodeIds: [n.id], state: n.state, kept: true }))
  return withBoxEdges(boxes, nodes, edges)
}

function withBoxEdges(boxes: MapBox[], nodes: readonly ComfyNodeInfo[], edges: readonly MapEdge[]): MapGraph {
  const boxOf = new Map<string, string>()
  for (const box of boxes) for (const id of box.nodeIds) boxOf.set(id, box.id)
  const states = new Map(nodes.map((n) => [n.id, n.state]))
  const byKey = new Map<string, MapBoxEdge>()
  for (const edge of edges) {
    const from = boxOf.get(edge.source)
    const to = boxOf.get(edge.target)
    if (!from || !to || from === to) continue
    const key = `${from}\u0000${to}`
    const state = edgeState(edge, states)
    const current = byKey.get(key)
    if (current) {
      current.edges.push(edge)
      current.state = strongerState(current.state, state)
    } else {
      byKey.set(key, { id: `e:${from}>${to}`, from, to, state, edges: [edge] })
    }
  }
  return { boxes, boxEdges: [...byKey.values()], boxOf }
}

export interface CollapseOptions {
  /** 箱の数の目安。超えるときは、同じ段・同じ状態の束をさらにまとめる。 */
  maxBoxes?: number
}

/**
 * 縮約した図(ADR-0052 4章)。`keep` のノードは1つずつ残し、それ以外を次の順で束ねる。
 * どの段階でも、束ねると図が循環する(束 A → 別の箱 → 束 B のような道がある)ものは束ねない。
 *
 * 束ねるのは同じ状態(出力につながる / 表示だけ / 使われない)で、同じ種類(`nodeCategory`)の束同士。
 *
 * 1. 鎖: 配線でつながるノード同士(`UNETLoader → LoRA → ModelSampling` など)。
 * 2. 読み込み: 入力の配線を持たないモデルの読み込みの束を1つにまとめる。
 * 3. 兄弟: 同じ段にあり、同じ箱へ出る(または同じ箱から入る)束。
 * 4. 使われない・表示だけの束は、種類を問わず状態ごとに1つにまとめる。
 * 5. まだ箱が `maxBoxes` を超えるなら、同じ段・同じ状態の束を種類を問わずまとめる。
 */
export function collapseGraph(
  nodes: readonly ComfyNodeInfo[],
  edges: readonly MapEdge[],
  keep: ReadonlySet<string>,
  options: CollapseOptions = {},
): MapGraph {
  const maxBoxes = options.maxBoxes ?? 18
  const stateOf = new Map(nodes.map((n) => [n.id, n.state]))
  const categoryOf = new Map(nodes.map((n) => [n.id, nodeCategory(n)]))
  // 束(グループ)の集合。union-find ではなく、id → 代表の対応を直接持つ(ノード数は高々数百)。
  const groupOf = new Map<string, string>()
  const members = new Map<string, string[]>()
  for (const node of nodes) {
    groupOf.set(node.id, node.id)
    members.set(node.id, [node.id])
  }
  const mergeable = (g: string) => !(members.get(g) ?? []).some((id) => keep.has(id))
  const groupState = (g: string): NodeState =>
    (members.get(g) ?? []).reduce<NodeState>((acc, id) => strongerState(acc, stateOf.get(id) ?? 'unused'), 'unused')
  /** 束の種類(`other` 以外の最初のもの)。 */
  const groupCategory = (g: string): NodeCategory =>
    (members.get(g) ?? []).map((id) => categoryOf.get(id) ?? 'other').find((c) => c !== 'other') ?? 'other'
  const sameCategory = (a: string, b: string) => {
    const ca = groupCategory(a)
    const cb = groupCategory(b)
    return ca === cb || ca === 'other' || cb === 'other'
  }

  // 束の間の配線(重複なし)。束ねるたびに作り直す(小さい図なので十分)。
  const groupEdges = () => {
    const succ = new Map<string, Set<string>>()
    const pred = new Map<string, Set<string>>()
    for (const g of members.keys()) {
      succ.set(g, new Set())
      pred.set(g, new Set())
    }
    for (const e of edges) {
      const a = groupOf.get(e.source)
      const b = groupOf.get(e.target)
      if (!a || !b || a === b) continue
      succ.get(a)?.add(b)
      pred.get(b)?.add(a)
    }
    return { succ, pred }
  }

  /** a と b を束ねると循環するか(a から b、または b から a へ、ほかの束を通る道がある)。 */
  const wouldCycle = (a: string, b: string, succ: Map<string, Set<string>>) => {
    const reaches = (from: string, to: string) => {
      const stack = [...(succ.get(from) ?? [])].filter((x) => x !== to)
      const seen = new Set(stack)
      while (stack.length > 0) {
        const cur = stack.pop() as string
        for (const next of succ.get(cur) ?? []) {
          if (next === to) return true
          if (!seen.has(next)) {
            seen.add(next)
            stack.push(next)
          }
        }
      }
      return false
    }
    return reaches(a, b) || reaches(b, a)
  }

  const merge = (a: string, b: string) => {
    const keepId = a
    const moved = members.get(b) ?? []
    for (const id of moved) groupOf.set(id, keepId)
    members.set(keepId, [...(members.get(keepId) ?? []), ...moved])
    members.delete(b)
  }

  /** 条件に合う組を見つけては束ねる。束ねられなくなるまで繰り返す。 */
  const mergeWhile = (pairs: (succ: Map<string, Set<string>>, pred: Map<string, Set<string>>) => [string, string][]) => {
    for (let guard = 0; guard < 1000; guard += 1) {
      const { succ, pred } = groupEdges()
      let merged = false
      for (const [a, b] of pairs(succ, pred)) {
        if (!members.has(a) || !members.has(b) || a === b) continue
        if (wouldCycle(a, b, succ)) continue
        merge(a, b)
        merged = true
        break
      }
      if (!merged) return
    }
  }

  const order = nodes.map((n) => n.id)
  const groupsInOrder = () => order.filter((id) => members.has(id))

  // 1. 鎖
  mergeWhile(() => {
    const pairs: [string, string][] = []
    for (const e of edges) {
      const a = groupOf.get(e.source) as string
      const b = groupOf.get(e.target) as string
      if (a === b || !mergeable(a) || !mergeable(b)) continue
      if (groupState(a) !== groupState(b) || !sameCategory(a, b)) continue
      pairs.push([a, b])
    }
    return pairs
  })

  // 2. 読み込み(入力の配線を持たない束同士は、束ねても循環しない)
  mergeWhile((_succ, pred) => {
    const groups = groupsInOrder().filter(
      (g) => mergeable(g) && groupState(g) === 'live' && groupCategory(g) === 'model' && (pred.get(g)?.size ?? 0) === 0,
    )
    return groups.slice(1).map((g) => [groups[0], g] as [string, string])
  })

  // 段(束の図での最長の道の長さ)。
  const layersOf = (pred: Map<string, Set<string>>) => {
    const layer = new Map<string, number>()
    const visiting = new Set<string>()
    const visit = (g: string): number => {
      const known = layer.get(g)
      if (known !== undefined) return known
      if (visiting.has(g)) return 0
      visiting.add(g)
      let l = 0
      for (const p of pred.get(g) ?? []) l = Math.max(l, visit(p) + 1)
      visiting.delete(g)
      layer.set(g, l)
      return l
    }
    for (const g of members.keys()) visit(g)
    return layer
  }

  const siblingPairs = (strict: boolean) => (succ: Map<string, Set<string>>, pred: Map<string, Set<string>>) => {
    const layer = layersOf(pred)
    const groups = groupsInOrder().filter(mergeable)
    const pairs: [string, string][] = []
    for (let i = 0; i < groups.length; i += 1) {
      for (let j = i + 1; j < groups.length; j += 1) {
        const a = groups[i]
        const b = groups[j]
        if (groupState(a) !== groupState(b)) continue
        if (groupState(a) !== 'live') continue
        if (layer.get(a) !== layer.get(b)) continue
        if (strict) {
          if (!sameCategory(a, b)) continue
          const sa = succ.get(a) ?? new Set()
          const pa = pred.get(a) ?? new Set()
          const shares =
            [...(succ.get(b) ?? [])].some((x) => sa.has(x)) || [...(pred.get(b) ?? [])].some((x) => pa.has(x))
          if (!shares) continue
        }
        pairs.push([a, b])
      }
    }
    return pairs
  }

  // 3. 兄弟
  mergeWhile(siblingPairs(true))

  // 4. 使われない・表示だけ
  for (const state of ['unused', 'display'] as const) {
    mergeWhile(() => {
      const groups = groupsInOrder().filter((g) => mergeable(g) && groupState(g) === state)
      const pairs: [string, string][] = []
      for (let i = 1; i < groups.length; i += 1) pairs.push([groups[0], groups[i]])
      for (let i = 1; i < groups.length; i += 1) for (let j = i + 1; j < groups.length; j += 1) pairs.push([groups[i], groups[j]])
      return pairs
    })
  }

  // 5. 多すぎるときは、同じ段の束を種類・共有を問わずまとめる
  if (members.size > maxBoxes) mergeWhile(siblingPairs(false))

  const boxes: MapBox[] = groupsInOrder().map((g) => {
    const ids = members.get(g) ?? [g]
    const kept = ids.length === 1 && keep.has(ids[0])
    return { id: ids.length === 1 ? ids[0] : `group:${g}`, nodeIds: ids, state: groupState(g), kept }
  })
  return withBoxEdges(boxes, nodes, edges)
}

// -- 強調 ----------------------------------------------------------------------

export interface Highlight {
  /** 道筋のノードと配線(金)。 */
  pathNodes: Set<string>
  pathEdges: Set<string>
  /** 差し込まない根拠のノードと配線(赤の破線)。 */
  evidenceNodes: Set<string>
  evidenceEdges: Set<string>
}

export const EMPTY_HIGHLIGHT: Highlight = {
  pathNodes: new Set(),
  pathEdges: new Set(),
  evidenceNodes: new Set(),
  evidenceEdges: new Set(),
}

/**
 * `start` から配線を下って `targets` のどれかに届く最短の道筋(ノードと配線)。選ばれなかった枝は辿らない。
 * 起点からは、まず最初の出力(画像の読み込みなら IMAGE)から出る配線だけで探す(MASK を透過に使うだけの
 * 近道を道筋にしないため)。届かなければ、ほかの出力も使う。届かなければ null。
 */
export function pathToTargets(
  start: string,
  edges: readonly MapEdge[],
  targets: ReadonlySet<string>,
): { nodes: string[]; edges: MapEdge[] } | null {
  if (targets.has(start)) return { nodes: [start], edges: [] }
  return searchPath(start, edges, targets, true) ?? searchPath(start, edges, targets, false)
}

function searchPath(
  start: string,
  edges: readonly MapEdge[],
  targets: ReadonlySet<string>,
  firstOutputOnly: boolean,
): { nodes: string[]; edges: MapEdge[] } | null {
  const out = new Map<string, MapEdge[]>()
  for (const e of edges) {
    if (!e.followed) continue
    if (firstOutputOnly && e.source === start && e.output !== 0) continue
    const list = out.get(e.source) ?? []
    list.push(e)
    out.set(e.source, list)
  }
  const prev = new Map<string, MapEdge | null>([[start, null]])
  const queue = [start]
  while (queue.length > 0) {
    const cur = queue.shift() as string
    for (const e of out.get(cur) ?? []) {
      if (prev.has(e.target)) continue
      prev.set(e.target, e)
      if (targets.has(e.target)) {
        const pathEdges: MapEdge[] = []
        let at: string = e.target
        for (let step = prev.get(at); step; step = prev.get(at)) {
          pathEdges.unshift(step)
          at = step.source
        }
        return { nodes: [start, ...pathEdges.map((p) => p.target)], edges: pathEdges }
      }
      queue.push(e.target)
    }
  }
  return null
}

/** 項目の値になっているノード(入力を指すものはそのノード)。 */
export function fieldNodes(bindings: BindingsFormState, field: FieldKey): string[] {
  const ref = (r: InputRefValue | null) => (setRef(r) ? [r.node] : [])
  switch (field) {
    case 'prompt':
      return ref(bindings.prompt)
    case 'negative_prompt':
      return ref(bindings.negativePrompt)
    case 'width':
      return ref(bindings.width)
    case 'height':
      return ref(bindings.height)
    case 'batch_size':
      return ref(bindings.batchSize)
    case 'seed':
      return bindings.seeds.filter(setRef).map((r) => r.node)
    case 'images':
      return bindings.images.filter(setRef).map((r) => r.node)
    case 'mask':
      if (bindings.maskMode === 'load_image_mask') return ref(bindings.maskRef)
      if (bindings.maskMode === 'image_alpha') return bindings.images.filter(setRef).slice(0, 1).map((r) => r.node)
      return []
    case 'outputs':
      return [...bindings.outputs]
    case 'final_prompt':
      return bindings.finalPrompt ? [bindings.finalPrompt] : []
  }
}

/**
 * 項目の強調。値があれば、そのノードから出力(無ければ保存ノード)までの道筋。出力は自身、
 * 最終プロンプトは表示している配線。値が無ければ、読み取りの根拠(`evidence`)と道筋の起点、
 * その間の配線。
 */
export function fieldHighlight(
  field: FieldKey,
  bindings: BindingsFormState,
  nodes: readonly ComfyNodeInfo[],
  edges: readonly MapEdge[],
  reading: ComfyBindingReading | undefined,
): Highlight {
  const h: Highlight = {
    pathNodes: new Set(),
    pathEdges: new Set(),
    evidenceNodes: new Set(),
    evidenceEdges: new Set(),
  }
  const starts = fieldNodes(bindings, field)
  if (starts.length > 0) {
    const outputs = bindings.outputs.length > 0 ? bindings.outputs : nodes.filter((n) => n.kind === 'save').map((n) => n.id)
    const targets = new Set(outputs)
    for (const start of starts) {
      h.pathNodes.add(start)
      if (field === 'outputs') continue
      if (field === 'final_prompt') {
        for (const e of edges) {
          if (e.target === start) {
            h.pathNodes.add(e.source)
            h.pathEdges.add(edgeKey(e))
          }
        }
        continue
      }
      const path = pathToTargets(start, edges, targets)
      if (!path) continue
      for (const id of path.nodes) h.pathNodes.add(id)
      for (const e of path.edges) h.pathEdges.add(edgeKey(e))
    }
    return h
  }
  if (!reading) return h
  for (const step of reading.evidence ?? []) h.evidenceNodes.add(step.node)
  // 道筋のある差し込まない項目(計算するノードから配線された幅など)は、その起点を根拠にする。
  for (const steps of reading.paths ?? []) if (steps.length > 0) h.evidenceNodes.add(steps[0].node)
  for (const e of edges) {
    if (h.evidenceNodes.has(e.source) && h.evidenceNodes.has(e.target)) h.evidenceEdges.add(edgeKey(e))
  }
  return h
}

/** 図のパネルで選んだ入力の扱い。 */
export type InputChoice = { kind: 'fixed' } | { kind: 'exposed' } | { kind: 'bind'; field: InputField }

/** 入力の今の扱いを、選択肢の値にする。 */
export function choiceOf(use: InputUse): InputChoice | null {
  switch (use.kind) {
    case 'wired':
      return null
    case 'fixed':
      return { kind: 'fixed' }
    case 'exposed':
      return { kind: 'exposed' }
    case 'bound': {
      const field = use.fields.find((f): f is InputField => (INPUT_FIELDS as readonly string[]).includes(f))
      return field ? { kind: 'bind', field } : { kind: 'fixed' }
    }
  }
}

export function choiceValue(choice: InputChoice): string {
  return choice.kind === 'bind' ? `bind:${choice.field}` : choice.kind
}

export function parseChoiceValue(value: string): InputChoice | null {
  if (value === 'fixed' || value === 'exposed') return { kind: value }
  if (value.startsWith('bind:')) {
    const field = value.slice('bind:'.length)
    if ((INPUT_FIELDS as readonly string[]).includes(field)) return { kind: 'bind', field: field as InputField }
  }
  return null
}

/** 公開できる値か(真偽値・数・文字列)。 */
export function canExpose(input: ComfyNodeInputInfo): boolean {
  return !input.linked && inferParamType(input.value) !== null
}

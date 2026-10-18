/**
 * 差し込み先のカード(ADR-0052 4章)の状態を計算する純粋関数。
 *
 * - 読み取り(analyze の `readings`)と、画面の値(`BindingsFormState`)を項目ごとに突き合わせる。
 * - 行の出どころ(`RowOrigin`): 読み取りの値のまま / 保存済みの値(編集画面)/ 手で選んだ値。
 * - 問いのカード: `ask` の項目のうち、まだ読み取りのままのもの。幅・高さ・枚数は1つの問いにまとめる。
 * - 検証の結果(サーバーの `ValidationIssue`)を行に振り分ける。規則そのものはサーバーにだけある。
 * - ノードの名前の見せ方(短い id、既定値の冒頭)と、行内のピッカーの選択肢。
 */
import type {
  ComfyAnalyzeResponse,
  ComfyBindingCandidate,
  ComfyBindingReading,
  ComfyNodeInfo,
  ComfyOperation,
  ComfyParamTierInfo,
  ComfySuggestedBindings,
  ComfyValidateRequest,
  ComfyValidationIssue,
} from '../../api/client'
import { bindingRefsList, bindingsFormFromSuggestion, type BindingsFormState } from './bindingsForm'
import { exposedParamRowToApi, validateExposedParamName, type ExposedParamRow } from './exposedParamsForm'
import type { InputRefValue } from './nodeOptions'

export type FieldKey = ComfyBindingReading['field']

/** 画面に出す順(サーバーの読み取りと同じ順)。 */
export const FIELD_ORDER: readonly FieldKey[] = [
  'prompt',
  'negative_prompt',
  'images',
  'mask',
  'seed',
  'width',
  'height',
  'batch_size',
  'outputs',
  'final_prompt',
]

/** 操作ごとに出す項目。generate は画像の枠とマスクを取らない(ADR-0013)。 */
export function visibleFields(operation: ComfyOperation): FieldKey[] {
  return FIELD_ORDER.filter((field) => operation === 'edit' || (field !== 'images' && field !== 'mask'))
}

/** 問いのカードの要素の id(行から問いへ移動するのに使う)。 */
export function questionCardId(id: QuestionId): string {
  return `question-${id}`
}

/** 行の要素の id(検証の一覧・問いのカードから移動するのに使う)。 */
export function bindingRowId(field: FieldKey): string {
  return `binding-row-${field}`
}

export const REQUIRED_FIELDS: ReadonlySet<FieldKey> = new Set(['prompt', 'outputs'])

export type Confidence = ComfyBindingReading['confidence']

/** 行の値の出どころ。 */
export type RowOrigin = 'reading' | 'saved' | 'manual'

export type RowOrigins = Record<FieldKey, RowOrigin>

export function initialOrigins(origin: RowOrigin): RowOrigins {
  return Object.fromEntries(FIELD_ORDER.map((field) => [field, origin])) as RowOrigins
}

/** 行のバッジ。読み取りのままなら確信度、それ以外は出どころ。 */
export type RowBadge = Confidence | 'manual' | 'saved'

export function rowBadge(reading: ComfyBindingReading | undefined, origin: RowOrigin): RowBadge {
  if (origin === 'manual') return 'manual'
  if (origin === 'saved') return 'saved'
  return reading?.confidence ?? 'absent'
}

export function readingsByField(
  readings: readonly ComfyBindingReading[] | null | undefined,
): Partial<Record<FieldKey, ComfyBindingReading>> {
  const map: Partial<Record<FieldKey, ComfyBindingReading>> = {}
  for (const reading of readings ?? []) map[reading.field] = reading
  return map
}

/** 最初から開いておく行(外れうる推定の行だけ。ADR-0052 4章の試作)。 */
export function initiallyExpanded(reading: ComfyBindingReading | undefined, origin: RowOrigin): boolean {
  // ask の行は問いのカードで答えるので開かない(「どれでもない」で開く)。
  if (origin !== 'reading' || !reading) return false
  return reading.confidence === 'ranked'
}

// -- 値の比べ方 ---------------------------------------------------------------

function refText(ref: InputRefValue | null | undefined): string | null {
  return ref && ref.node !== '' && ref.input !== '' ? `${ref.node}\u0000${ref.input}` : null
}

/** 項目の値を比べられる文字列にする(未設定は null)。 */
export function fieldValueKey(form: BindingsFormState, field: FieldKey): string | null {
  switch (field) {
    case 'prompt':
      return refText(form.prompt)
    case 'negative_prompt':
      return refText(form.negativePrompt)
    case 'width':
      return refText(form.width)
    case 'height':
      return refText(form.height)
    case 'batch_size':
      return refText(form.batchSize)
    case 'seed': {
      const refs = form.seeds.map(refText).filter((v): v is string => v !== null)
      return refs.length > 0 ? [...refs].sort().join('|') : null
    }
    case 'images': {
      const refs = form.images.map(refText).filter((v): v is string => v !== null)
      return refs.length > 0 ? refs.join('|') : null
    }
    case 'mask':
      if (form.maskMode === 'none') return null
      if (form.maskMode === 'image_alpha') return 'image_alpha'
      return form.maskRef ? `load_image_mask|${refText(form.maskRef)}` : null
    case 'outputs':
      return form.outputs.length > 0 ? [...form.outputs].sort().join('|') : null
    case 'final_prompt':
      return form.finalPrompt
  }
}

export type ReadingComparison = 'match' | 'differ'

/** 保存済みの値と、今の読み取りの値を比べる(編集画面。ADR-0052 3章)。 */
export function compareWithReading(
  form: BindingsFormState,
  suggested: ComfySuggestedBindings | null | undefined,
  field: FieldKey,
): ReadingComparison {
  const reading = bindingsFormFromSuggestion(suggested)
  return fieldValueKey(form, field) === fieldValueKey(reading, field) ? 'match' : 'differ'
}

/**
 * 編集画面の行に出す比べた結果。読み取りが決めきれなかった項目(`ask`)は、比べる値が無いので
 * 「違う」とは言わずに `undecided` にする。
 */
export function savedComparison(
  form: BindingsFormState,
  suggested: ComfySuggestedBindings | null | undefined,
  reading: ComfyBindingReading | undefined,
  field: FieldKey,
): ReadingComparison | 'undecided' {
  if (reading?.confidence === 'ask') return 'undecided'
  return compareWithReading(form, suggested, field)
}

/** 項目の値を、読み取り(analyze の提案)の値に置き換える。 */
export function withReadingValue(
  form: BindingsFormState,
  suggested: ComfySuggestedBindings | null | undefined,
  field: FieldKey,
): BindingsFormState {
  const reading = bindingsFormFromSuggestion(suggested)
  switch (field) {
    case 'prompt':
      return { ...form, prompt: reading.prompt }
    case 'negative_prompt':
      return { ...form, negativePrompt: reading.negativePrompt }
    case 'width':
      return { ...form, width: reading.width }
    case 'height':
      return { ...form, height: reading.height }
    case 'batch_size':
      return { ...form, batchSize: reading.batchSize }
    case 'seed':
      return { ...form, seeds: reading.seeds }
    case 'images':
      return { ...form, images: reading.images }
    case 'mask':
      return { ...form, maskMode: reading.maskMode, maskRef: reading.maskRef }
    case 'outputs':
      return { ...form, outputs: reading.outputs }
    case 'final_prompt':
      return { ...form, finalPrompt: reading.finalPrompt }
  }
}

/** 項目に値が入っているか(差し込むか)。 */
export function fieldIsSet(form: BindingsFormState, field: FieldKey): boolean {
  return fieldValueKey(form, field) !== null
}

/**
 * 差し込み先を手で変えたあとの出どころ。値が変わった項目と、触った項目(`touched`)を「手で選択」にする。
 */
export function originsAfterChange(
  origins: RowOrigins,
  before: BindingsFormState,
  after: BindingsFormState,
  touched: readonly FieldKey[] = [],
): RowOrigins {
  const next = { ...origins }
  for (const field of FIELD_ORDER) {
    if (touched.includes(field) || fieldValueKey(before, field) !== fieldValueKey(after, field)) next[field] = 'manual'
  }
  return next
}

/**
 * ファイルを差し替えたあとの出どころ。新しい読み取りと同じ値の項目は「読み取り」に、
 * 違う項目で読み取りのままだったものは `fallback`(新規は手で選択、編集は保存済み)にする。
 */
export function originsAfterReplace(
  origins: RowOrigins,
  form: BindingsFormState,
  suggested: ComfySuggestedBindings | null | undefined,
  fallback: RowOrigin,
): RowOrigins {
  const next = { ...origins }
  for (const field of FIELD_ORDER) {
    if (compareWithReading(form, suggested, field) === 'match') next[field] = 'reading'
    else if (next[field] === 'reading') next[field] = fallback
  }
  return next
}

// -- 要約 ---------------------------------------------------------------------

export interface ReadingSummary {
  total: number
  /** 読み取りで決まった(要確認でない)項目の数。 */
  decided: number
  /** 確かめてほしい項目(問いが残っている項目)の数。 */
  toConfirm: number
  counts: Partial<Record<RowBadge, number>>
}

export function summarizeRows(
  fields: readonly FieldKey[],
  readings: Partial<Record<FieldKey, ComfyBindingReading>>,
  origins: RowOrigins,
): ReadingSummary {
  const counts: Partial<Record<RowBadge, number>> = {}
  let toConfirm = 0
  for (const field of fields) {
    const badge = rowBadge(readings[field], origins[field])
    counts[badge] = (counts[badge] ?? 0) + 1
    if (badge === 'ask') toConfirm += 1
  }
  return { total: fields.length, decided: fields.length - toConfirm, toConfirm, counts }
}

// -- 問いのカード ---------------------------------------------------------------

export type QuestionId = 'prompt' | 'negative_prompt' | 'size' | 'images' | 'mask'

export interface PendingQuestion {
  id: QuestionId
  /** 答えると決まる項目(幅・高さ・枚数は1つの問い)。 */
  fields: FieldKey[]
  reading: ComfyBindingReading
}

const SIZE_FIELDS: FieldKey[] = ['width', 'height', 'batch_size']

/** まだ答えていない問い。読み取りのままの `ask` の項目だけ。 */
export function pendingQuestions(
  fields: readonly FieldKey[],
  readings: Partial<Record<FieldKey, ComfyBindingReading>>,
  origins: RowOrigins,
): PendingQuestion[] {
  const questions: PendingQuestion[] = []
  const isPending = (field: FieldKey) =>
    fields.includes(field) && origins[field] === 'reading' && readings[field]?.confidence === 'ask'
  for (const field of ['prompt', 'negative_prompt', 'images', 'mask'] as const) {
    const reading = readings[field]
    if (reading && isPending(field)) questions.push({ id: field, fields: [field], reading })
  }
  const sizeFields = SIZE_FIELDS.filter(isPending)
  const sizeReading = sizeFields.length > 0 ? readings[sizeFields[0]] : undefined
  if (sizeReading) questions.push({ id: 'size', fields: sizeFields, reading: sizeReading })
  return questions
}

/** 問いのある項目か(行に「上の問いに答えてください」を出す)。 */
export function questionForField(questions: readonly PendingQuestion[], field: FieldKey): PendingQuestion | undefined {
  return questions.find((q) => q.fields.includes(field))
}

function inputUsable(nodes: readonly ComfyNodeInfo[], node: string, input: string): boolean {
  const info = nodes.find((n) => n.id === node)
  const found = info?.inputs?.find((i) => i.name === input)
  return found !== undefined && !found.linked
}

/**
 * 問いに答える。候補(`candidate`)を差し込み先にした値と、手で決めた項目を返す。
 * 画像の枠は「この順でよい」(候補は使わない)で今の順を確かめたことにする。
 */
export function applyAnswer(
  form: BindingsFormState,
  question: PendingQuestion,
  candidate: ComfyBindingCandidate | null,
  nodes: readonly ComfyNodeInfo[],
): BindingsFormState {
  switch (question.id) {
    case 'prompt':
      return candidate?.input ? { ...form, prompt: { node: candidate.node, input: candidate.input } } : form
    case 'negative_prompt':
      return candidate?.input ? { ...form, negativePrompt: { node: candidate.node, input: candidate.input } } : form
    case 'mask':
      return candidate?.input
        ? { ...form, maskMode: 'load_image_mask', maskRef: { node: candidate.node, input: candidate.input } }
        : form
    case 'images':
      return form
    case 'size': {
      if (!candidate) return form
      const ref = (input: string): InputRefValue | null =>
        inputUsable(nodes, candidate.node, input) ? { node: candidate.node, input } : null
      return { ...form, width: ref('width'), height: ref('height'), batchSize: ref('batch_size') }
    }
  }
}

// -- 検証の結果 -------------------------------------------------------------------

/** 検証の行き先: 差し込み先の行、公開パラメーター、どこでもない(操作など)。 */
export type IssueTarget = { kind: 'field'; field: FieldKey } | { kind: 'exposed'; name: string } | { kind: 'general' }

/** サーバーの `field`(`seed[0]`、`images[1]`、`exposed_params.steps` など)を行に振り分ける。 */
export function issueTarget(issue: Pick<ComfyValidationIssue, 'field'>): IssueTarget {
  const raw = issue.field ?? ''
  if (raw.startsWith('exposed_params.')) return { kind: 'exposed', name: raw.slice('exposed_params.'.length) }
  const base = raw.replace(/\[\d+\]$/, '')
  if ((FIELD_ORDER as readonly string[]).includes(base)) return { kind: 'field', field: base as FieldKey }
  return { kind: 'general' }
}

export function issuesForField(issues: readonly ComfyValidationIssue[], field: FieldKey): ComfyValidationIssue[] {
  return issues.filter((issue) => {
    const target = issueTarget(issue)
    return target.kind === 'field' && target.field === field
  })
}

export function splitIssues(issues: readonly ComfyValidationIssue[]): {
  errors: ComfyValidationIssue[]
  warnings: ComfyValidationIssue[]
} {
  return {
    errors: issues.filter((issue) => issue.level === 'error'),
    warnings: issues.filter((issue) => issue.level === 'warning'),
  }
}

/** 行の左端の帯(エラーが警告より強い)。 */
export function rowLevel(issues: readonly ComfyValidationIssue[]): 'error' | 'warning' | null {
  if (issues.some((issue) => issue.level === 'error')) return 'error'
  if (issues.length > 0) return 'warning'
  return null
}

/** 検証の API に送る本文。名前の形が正しくない公開パラメーターは送らない(画面が別に示す)。 */
export function buildValidateRequest(input: {
  template: Record<string, unknown>
  operation: ComfyOperation
  bindings: BindingsFormState
  exposedRows: readonly ExposedParamRow[]
}): ComfyValidateRequest {
  const b = input.bindings
  const set = (ref: InputRefValue | null) => (refText(ref) ? ref : null)
  return {
    template: input.template,
    operation: input.operation,
    bindings: {
      prompt: set(b.prompt),
      negative_prompt: set(b.negativePrompt),
      seed: b.seeds.filter((ref) => refText(ref) !== null),
      width: set(b.width),
      height: set(b.height),
      batch_size: set(b.batchSize),
      images: b.images.filter((ref) => refText(ref) !== null),
      mask:
        b.maskMode === 'image_alpha'
          ? { mode: 'image_alpha', node: null, input: null }
          : b.maskMode === 'load_image_mask' && b.maskRef
            ? { mode: 'load_image_mask', node: b.maskRef.node, input: b.maskRef.input }
            : null,
      outputs: b.outputs,
      final_prompt: b.finalPrompt,
    },
    exposed_params: input.exposedRows
      .filter((row) => row.enabled && validateExposedParamName(row.name) === null)
      .map(exposedParamRowToApi),
  }
}

// -- ノードの名前 -------------------------------------------------------------------

/**
 * ノードの id を短くする。サブグラフを展開した id(`459:474`)は先頭と末尾だけにする。
 * 完全な id はツールチップに出す。
 */
export function shortNodeId(id: string): string {
  const parts = id.split(':')
  if (parts.length === 1) return `#${id}`
  const head = parts[0]
  const last = parts[parts.length - 1]
  return parts.length === 2 ? `${head}›#${last}` : `${head}›…›#${last}`
}

/** 既定値の冒頭(文字列だけ)。長いものは `max` 文字で切って「…」を付ける。改行は空白にする。 */
export function valuePreview(value: unknown, max: number): string | null {
  if (typeof value !== 'string') return null
  const flat = value.replace(/\s+/g, ' ').trim()
  if (flat.length <= max) return flat
  return `${flat.slice(0, max)}…`
}

/** タイトルを主にするか(クラス名と同じタイトルは出さない)。 */
export function nodeDisplayTitle(node: Pick<ComfyNodeInfo, 'title' | 'class_type'> | undefined): string | null {
  if (!node?.title) return null
  return node.title === node.class_type ? null : node.title
}

export function inputValue(nodes: readonly ComfyNodeInfo[], ref: InputRefValue | null | undefined): unknown {
  if (!ref) return undefined
  return nodes.find((n) => n.id === ref.node)?.inputs?.find((i) => i.name === ref.input)?.value
}

// -- 行内のピッカーの選択肢 --------------------------------------------------------------

type ValueKind = 'string' | 'int' | 'image'

const FIELD_KIND: Partial<Record<FieldKey, ValueKind>> = {
  prompt: 'string',
  negative_prompt: 'string',
  seed: 'int',
  width: 'int',
  height: 'int',
  batch_size: 'int',
  images: 'image',
  mask: 'image',
}

/** 名前で先に出す入力(候補の並びを助けるだけ。正しいかはサーバーの検証が決める)。 */
const NAME_HINTS: Partial<Record<FieldKey, { classes?: string[]; inputs: string[] }>> = {
  seed: { inputs: ['seed', 'noise_seed'] },
  width: { inputs: ['width'] },
  height: { inputs: ['height'] },
  batch_size: { inputs: ['batch_size'] },
  images: { classes: ['LoadImage'], inputs: ['image'] },
  mask: { classes: ['LoadImageMask'], inputs: ['image'] },
}

function kindMatches(value: unknown, kind: ValueKind): boolean {
  if (kind === 'int') return typeof value === 'number' && Number.isInteger(value)
  return typeof value === 'string'
}

export interface PickerOption {
  node: string
  input: string
  /** 配線された入力(選べない)。 */
  wired: boolean
  state: ComfyNodeInfo['state']
}

export interface PickerOptions {
  candidates: PickerOption[]
  others: PickerOption[]
  /** 絞り込みで隠した「ほかの入力」の数(多すぎるときは先頭だけ出す)。 */
  hiddenCount: number
}

function nodeMatches(node: ComfyNodeInfo, input: string, query: string): boolean {
  if (query === '') return true
  const q = query.toLowerCase()
  return [node.id, node.class_type ?? '', node.title ?? '', input].some((text) => text.toLowerCase().includes(q))
}

/**
 * 1つの入力を選ぶピッカーの選択肢。候補(読み取りの候補、名前の合う入力)が先、ほかの入力が後。
 * 値の種類が合わない入力は出さない。配線された入力は出すが選べない。
 */
export function pickerOptions(
  nodes: readonly ComfyNodeInfo[],
  field: FieldKey,
  readingCandidates: readonly ComfyBindingCandidate[],
  query: string,
  limit = 60,
): PickerOptions {
  const kind = FIELD_KIND[field]
  const hints = NAME_HINTS[field]
  const seen = new Set<string>()
  const candidates: PickerOption[] = []
  const others: PickerOption[] = []
  const byId = new Map(nodes.map((n) => [n.id, n]))

  const push = (list: PickerOption[], node: ComfyNodeInfo, input: string, wired: boolean) => {
    const key = `${node.id}\u0000${input}`
    if (seen.has(key)) return
    seen.add(key)
    if (!nodeMatches(node, input, query)) return
    list.push({ node: node.id, input, wired, state: node.state })
  }

  for (const candidate of readingCandidates) {
    const node = byId.get(candidate.node)
    if (!node) continue
    const inputs = candidate.input ? [candidate.input] : field === 'width' || field === 'height' || field === 'batch_size' ? [field] : []
    for (const name of inputs) {
      const info = node.inputs?.find((i) => i.name === name)
      if (info) push(candidates, node, name, info.linked)
    }
  }
  if (hints) {
    for (const node of nodes) {
      if (hints.classes && !hints.classes.includes(node.class_type ?? '')) continue
      for (const info of node.inputs ?? []) {
        if (!hints.inputs.includes(info.name) || info.linked) continue
        if (kind && !kindMatches(info.value, kind)) continue
        push(candidates, node, info.name, false)
      }
    }
  }
  // 生きているノードを先に。
  const ordered = [...nodes].sort((a, b) => stateRank(a.state) - stateRank(b.state))
  const wiredTail: PickerOption[] = []
  for (const node of ordered) {
    for (const info of node.inputs ?? []) {
      if (info.linked) {
        push(wiredTail, node, info.name, true)
        continue
      }
      if (kind && !kindMatches(info.value, kind)) continue
      push(others, node, info.name, false)
    }
  }
  const all = [...others, ...wiredTail]
  return { candidates, others: all.slice(0, limit), hiddenCount: Math.max(0, all.length - limit) }
}

function stateRank(state: ComfyNodeInfo['state']): number {
  return state === 'live' ? 0 : state === 'display' ? 1 : 2
}

/** ノードそのものを選ぶピッカー(出力、最終プロンプト)の選択肢。候補が先。 */
export function nodePickerOptions(
  nodes: readonly ComfyNodeInfo[],
  field: 'outputs' | 'final_prompt',
  readingNodes: readonly string[],
  query: string,
): { candidates: ComfyNodeInfo[]; others: ComfyNodeInfo[] } {
  const isCandidate = (node: ComfyNodeInfo) => {
    if (readingNodes.includes(node.id)) return true
    const cls = node.class_type ?? ''
    if (field === 'outputs') return /Save/.test(cls)
    return /PreviewAny|ShowText|PreviewText|DisplayText/i.test(cls)
  }
  const matches = (node: ComfyNodeInfo) => nodeMatches(node, '', query)
  const ordered = [...nodes].sort((a, b) => stateRank(a.state) - stateRank(b.state))
  return {
    candidates: ordered.filter((n) => isCandidate(n) && matches(n)),
    others: ordered.filter((n) => !isCandidate(n) && matches(n)),
  }
}

// -- 公開パラメーターの3段 -----------------------------------------------------------------

export type ParamTier = ComfyParamTierInfo['tier']
export type HiddenReason = NonNullable<ComfyParamTierInfo['hidden_reason']> | 'binding'

export interface TieredRow {
  index: number
  row: ExposedParamRow
}

export interface TieredRows {
  recommended: TieredRow[]
  other: TieredRow[]
  hidden: (TieredRow & { reason: HiddenReason | null })[]
}

function tierKey(node: string, input: string): string {
  return `${node}\u0000${input}`
}

/**
 * 公開パラメーターの行を3段に分ける。公開している行は段にかかわらず「推奨」に出す(外せるように)。
 * 差し込み先に使っている入力は公開しない段に回す。
 */
export function tierExposedRows(
  rows: readonly ExposedParamRow[],
  tiers: readonly ComfyParamTierInfo[] | null | undefined,
  bindings: BindingsFormState,
): TieredRows {
  const tierMap = new Map((tiers ?? []).map((t) => [tierKey(t.node, t.input), t]))
  const bound = new Set(bindingRefsList(bindings).map((ref) => tierKey(ref.node, ref.input)))
  const result: TieredRows = { recommended: [], other: [], hidden: [] }
  rows.forEach((row, index) => {
    const key = tierKey(row.node, row.input)
    if (bound.has(key)) {
      result.hidden.push({ index, row, reason: 'binding' })
      return
    }
    const info = tierMap.get(key)
    if (row.enabled || info?.tier === 'recommended') {
      result.recommended.push({ index, row })
    } else if (info?.tier === 'hidden') {
      result.hidden.push({ index, row, reason: info.hidden_reason ?? null })
    } else {
      result.other.push({ index, row })
    }
  })
  return result
}

/** 新規登録の初期値: 推奨の段の行を最初から公開にする。 */
export function enableRecommendedRows(
  rows: readonly ExposedParamRow[],
  tiers: readonly ComfyParamTierInfo[] | null | undefined,
): ExposedParamRow[] {
  const recommended = new Set(
    (tiers ?? []).filter((t) => t.tier === 'recommended').map((t) => tierKey(t.node, t.input)),
  )
  return rows.map((row) => (recommended.has(tierKey(row.node, row.input)) ? { ...row, enabled: true } : row))
}

/** 差し込み先に使っている入力は公開しない(保存・検証に送る行)。 */
export function effectiveExposedRows(rows: readonly ExposedParamRow[], bindings: BindingsFormState): ExposedParamRow[] {
  const bound = new Set(bindingRefsList(bindings).map((ref) => tierKey(ref.node, ref.input)))
  return rows.map((row) => (row.enabled && bound.has(tierKey(row.node, row.input)) ? { ...row, enabled: false } : row))
}

/** analyze の結果のうち、カードの画面が使う部分。 */
export type CardAnalysis = Pick<ComfyAnalyzeResponse, 'readings' | 'param_tiers' | 'suggested_bindings' | 'edges'>

/** 保存の直後に開いた印(新規登録の保存で編集画面へ移ったとき、保存後のカードを出す)。 */
export function isJustSaved(state: unknown): boolean {
  return typeof state === 'object' && state !== null && (state as Record<string, unknown>).justSaved === true
}

/**
 * analyze の警告のうち、ファイルの節に出すもの。読み取り(`readings`)で同じことを示している警告
 * (プロンプトの候補が複数、出力が見つからない、など)はカードと重なるので出さない。
 */
export function generalWarningMessages(items: ComfyAnalyzeResponse['warning_items']): string[] {
  const messages: string[] = []
  for (const item of items ?? []) {
    if (item.kind === 'general' && !messages.includes(item.message)) messages.push(item.message)
  }
  return messages
}

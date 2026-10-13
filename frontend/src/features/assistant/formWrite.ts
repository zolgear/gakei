/**
 * アシスタントの生成フォームの書き込み(`update_form`。ADR-0048 11章)の純粋関数。
 *
 * - サーバーは値を検証して道具の結果(`patch` = 変えた項目、`form` = 重ねた後の値)に入れるだけで、
 *   フォームの値を保存しない。ブラウザは、その発言の応答(SSE)を受けている間に届いた成功の結果
 *   だけをフォームに反映する。会話を開き直したとき・履歴を読んだときは反映しない
 *   (`formWriteFromStreamEvent` は SSE のイベントだけを受ける)。
 * - 反映は、今のフォームの値(RunFormContext の `formState`)に `patch` を重ねた全体の値を作り、
 *   `setFormState` と `requestFormLoad({ kind: 'assistant' })` に渡す(Run 詳細の「同じ設定で
 *   新規作成」と同じく、スタジオがマウント済みでも未マウントでも入る)。
 * - 「元に戻す」は、反映の直前に取った値で同じように全体を戻す。変更前の値はそのページを開いて
 *   いる間だけ持ち、会話には保存しない。
 */
import type { AssistantMessageResponse, AssistantStreamEvent } from '../../api/client'
import { newInputId } from '../run-form/editInputs'
import type { RunFormState, RunInputItem } from '../run-form/types'

export const UPDATE_FORM_TOOL = 'update_form'
/** カードでプロンプトを省略せずに出す長さ(文字)。 */
export const FORM_PROMPT_PREVIEW_MAX = 160

type ParamValue = string | number | boolean
type InputRole = RunInputItem['role']

export interface FormPatchInput {
  assetId: string
  role: InputRole
}

/** 道具の結果の `patch`(変えた項目だけ。無い項目は変えない)。 */
export interface FormPatch {
  provider?: string
  model?: string
  prompt?: string
  /** null は既定に戻す(戻した値は `formParams` にある。無ければ未指定)。 */
  params?: Record<string, ParamValue | null>
  inputs?: FormPatchInput[]
  /** キーがあれば変える。null はグループなし。 */
  assetGroupId?: string | null
}

export interface FormWrite {
  patch: FormPatch
  /** 重ねた後のパラメーター(サーバーが既定を埋め、モデルの切り替えで使えない値を落とした値)。 */
  formParams: Record<string, ParamValue>
  /** 重ねた後のプロバイダーとモデル(パラメーターの表示名を引くため。分からなければ空)。 */
  formProvider: string
  formModel: string
}

/** 反映した書き込みの記録(ページを開いている間だけ持つ)。 */
export interface FormWriteRecord {
  before: RunFormState
  after: RunFormState
  undone: boolean
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function isParamValue(value: unknown): value is ParamValue {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
}

function readParams(value: unknown): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {}
  for (const [key, v] of Object.entries(asRecord(value) ?? {})) if (isParamValue(v)) out[key] = v
  return out
}

function readRole(value: unknown): InputRole {
  return value === 'mask' || value === 'reference' ? value : 'image'
}

/** `update_form` の成功の結果を読む。それ以外(失敗、ほかの道具、形の崩れ)は null。 */
export function readFormWrite(message: AssistantMessageResponse): FormWrite | null {
  if (message.role !== 'tool' || message.ok !== true || message.tool_name !== UPDATE_FORM_TOOL) return null
  const result = asRecord(message.result)
  const rawPatch = asRecord(result?.patch)
  if (!result || !rawPatch) return null
  const patch: FormPatch = {}
  if (typeof rawPatch.provider === 'string') patch.provider = rawPatch.provider
  if (typeof rawPatch.model === 'string') patch.model = rawPatch.model
  if (typeof rawPatch.prompt === 'string') patch.prompt = rawPatch.prompt
  const rawParams = asRecord(rawPatch.params)
  if (rawParams) {
    patch.params = {}
    for (const [key, v] of Object.entries(rawParams)) {
      if (v === null || isParamValue(v)) patch.params[key] = v
    }
  }
  if (Array.isArray(rawPatch.inputs)) {
    patch.inputs = rawPatch.inputs
      .map(asRecord)
      .filter((i): i is Record<string, unknown> => i !== null && typeof i.asset_id === 'string')
      .map((i) => ({ assetId: i.asset_id as string, role: readRole(i.role) }))
  }
  if ('asset_group_id' in rawPatch) {
    const group = rawPatch.asset_group_id
    patch.assetGroupId = typeof group === 'string' ? group : null
  }
  const form = asRecord(result.form)
  return {
    patch,
    formParams: readParams(form?.params),
    formProvider: typeof form?.provider === 'string' ? form.provider : '',
    formModel: typeof form?.model === 'string' ? form.model : '',
  }
}

/**
 * SSE のイベントから、反映する書き込みを取り出す。`tool_result` の `update_form` の成功だけ。
 * 会話の読み込み(`GET /api/assistant/conversations/{id}`)はここを通らないので、開き直しても
 * 昔の書き込みを反映し直さない。
 */
export function formWriteFromStreamEvent(event: AssistantStreamEvent): FormWrite | null {
  if (event.type !== 'tool_result') return null
  return readFormWrite(event.message)
}

/** 入力画像を、役割ごとの並び順を付けてフォームの形にする。 */
function toInputItems(inputs: readonly FormPatchInput[], makeId: () => string): RunInputItem[] {
  const positions: Partial<Record<InputRole, number>> = {}
  return inputs.map((input) => {
    const position = positions[input.role] ?? 0
    positions[input.role] = position + 1
    return { inputId: makeId(), assetId: input.assetId, role: input.role, position }
  })
}

/**
 * 今のフォームの値に書き込みを重ねた値。プロバイダーかモデルを変えたときのパラメーターは、
 * サーバーが引き継ぎ・既定を計算した `formParams` をそのまま使う。変えないときは、指定された
 * キーだけを変える(null は `formParams` の既定、無ければ未指定)。
 */
export function applyFormWrite(
  current: RunFormState,
  write: FormWrite,
  makeId: () => string = newInputId,
): RunFormState {
  const { patch } = write
  const next: RunFormState = { ...current, params: { ...current.params } }
  if (patch.provider !== undefined) next.provider = patch.provider
  if (patch.model !== undefined) next.model = patch.model
  if (patch.prompt !== undefined) next.prompt = patch.prompt
  if (patch.provider !== undefined || patch.model !== undefined) {
    next.params = { ...write.formParams }
  } else if (patch.params) {
    for (const [key, value] of Object.entries(patch.params)) {
      if (value !== null) {
        next.params[key] = value
      } else if (write.formParams[key] !== undefined) {
        next.params[key] = write.formParams[key]
      } else {
        delete next.params[key]
      }
    }
  }
  if (patch.inputs !== undefined) next.inputs = toInputItems(patch.inputs, makeId)
  if (patch.assetGroupId !== undefined) next.assetGroupId = patch.assetGroupId
  return next
}

// -- カードに出す変更前と変更後 ------------------------------------------------------

export type FormChange =
  | { field: 'model'; before: string | null; after: string }
  | { field: 'prompt'; before: string | null; after: string }
  | { field: 'param'; key: string; before: ParamValue | null | undefined; after: ParamValue | null }
  | { field: 'inputs'; before: FormPatchInput[] | null; after: FormPatchInput[] }
  | { field: 'group'; before: string | null | undefined; after: string | null }

function modelLabel(provider: string, model: string, withProvider: boolean): string {
  return withProvider && provider ? `${provider} / ${model}` : model
}

function sortedInputs(inputs: readonly RunInputItem[]): FormPatchInput[] {
  const order: Record<InputRole, number> = { image: 0, reference: 1, mask: 2 }
  return [...inputs]
    .sort((a, b) => order[a.role] - order[b.role] || a.position - b.position)
    .map((i) => ({ assetId: i.assetId, role: i.role }))
}

// 操作を generate にすると、入力画像が元から空でも patch に `inputs: []` が入る。変わらない項目は
// カードに出さない。
function sameInputs(a: readonly FormPatchInput[], b: readonly FormPatchInput[]): boolean {
  return a.length === b.length && a.every((x, i) => x.assetId === b[i].assetId && x.role === b[i].role)
}

/**
 * カードに出す変更の一覧。`before` は反映の直前のフォームの値(会話を開き直したなどで無ければ
 * null。そのときは変更後だけを出す)。変更後は書き込みの結果から作る(反映先のフォームが後で
 * 変わっても、カードはその書き込みの内容を出す)。パラメーターの変更後の null は「未指定」。
 * モデルを変えて落ちたパラメーターも、変更前があれば出す。
 */
export function describeFormWrite(write: FormWrite, before: RunFormState | null): FormChange[] {
  const { patch } = write
  const changes: FormChange[] = []
  const modelChanged = patch.provider !== undefined || patch.model !== undefined
  if (modelChanged) {
    const provider = patch.provider ?? before?.provider ?? ''
    const model = patch.model ?? before?.model ?? ''
    const withProvider = before !== null && before.provider !== provider
    changes.push({
      field: 'model',
      before: before ? modelLabel(before.provider, before.model, withProvider) : null,
      after: modelLabel(provider, model, withProvider || patch.provider !== undefined),
    })
  }
  if (patch.prompt !== undefined) {
    changes.push({ field: 'prompt', before: before ? before.prompt : null, after: patch.prompt })
  }
  const keys = new Set(Object.keys(patch.params ?? {}))
  if (modelChanged && before) {
    for (const key of new Set([...Object.keys(before.params), ...Object.keys(write.formParams)])) {
      if (before.params[key] !== write.formParams[key]) keys.add(key)
    }
  }
  for (const key of [...keys].sort()) {
    const requested = patch.params?.[key]
    const after = requested === undefined || requested === null ? (write.formParams[key] ?? null) : requested
    const beforeValue = before ? (before.params[key] ?? null) : undefined
    if (before && beforeValue === after) continue
    changes.push({ field: 'param', key, before: beforeValue, after })
  }
  if (patch.inputs !== undefined) {
    const beforeInputs = before ? sortedInputs(before.inputs) : null
    if (!beforeInputs || !sameInputs(beforeInputs, patch.inputs)) {
      changes.push({ field: 'inputs', before: beforeInputs, after: patch.inputs })
    }
  }
  if (patch.assetGroupId !== undefined && !(before && before.assetGroupId === patch.assetGroupId)) {
    changes.push({ field: 'group', before: before ? before.assetGroupId : undefined, after: patch.assetGroupId })
  }
  return changes
}

/** 長いプロンプトを省略する(カードの表示用)。 */
export function previewPrompt(text: string, max = FORM_PROMPT_PREVIEW_MAX): { text: string; truncated: boolean } {
  if (text.length <= max) return { text, truncated: false }
  return { text: `${text.slice(0, max)}…`, truncated: true }
}

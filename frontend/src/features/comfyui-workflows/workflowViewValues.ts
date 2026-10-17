/**
 * 生成画面からのワークフローの閲覧(ADR-0052 7章)で、差し込む入力と公開パラメーターに添える値を決める純粋関数。
 *
 * 値の出どころ:
 * - `form`: 今のフォームの値
 * - `default`: 公開パラメーターの登録した既定値(フォームが未入力のとき、実行ではこれが入る)
 * - `template`: テンプレートの値(未入力で、既定値も無いとき)
 * - `random`: seed が未入力(実行のたびにサーバーが決める)
 * - `input`: 入力画像・マスク(フォームの値ではなく、入力欄の画像を差し込む)
 * - `redacted`: 秘密に見える値として伏せてある(ADR-0013 8章)
 *
 * 実行時の差し込み(`resolve_prompt`)と同じ順で決める。
 */
import type {
  ComfyViewExposedParam,
  ComfyViewNode,
  ComfyViewNodeInput,
} from '../../api/client'
import type { InputField } from './bindingGraph'
import type { FieldKey } from './bindingCards'
import type { BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'

export type ValueSource = 'form' | 'default' | 'template' | 'random' | 'input' | 'redacted'

export interface ShownValue {
  source: ValueSource
  /** 表示する値(`random`・`input`・`redacted` では null)。 */
  value: unknown
  /** 画像の枠の番号(1 始まり。`input` の画像のときだけ)。 */
  slot?: number
}

/** 生成画面のフォームのうち、閲覧で使う部分。 */
export interface ViewFormValues {
  prompt: string
  params: Readonly<Record<string, string | number | boolean>>
}

/** フォームの値が入っているか(未指定の空文字・null は入っていない扱い)。 */
export function isFormValueSet(value: unknown): boolean {
  return value !== undefined && value !== null && value !== ''
}

function templateOrRedacted(input: ComfyViewNodeInput | undefined): ShownValue {
  if (input?.redacted) return { source: 'redacted', value: null }
  return { source: 'template', value: input?.value ?? null }
}

/** 差し込み先の項目と、フォームのパラメーター名の対応(プロンプトは別欄、画像とマスクは入力欄)。 */
const FIELD_PARAM: Partial<Record<InputField, string>> = {
  negative_prompt: 'negative_prompt',
  seed: 'seed',
  width: 'width',
  height: 'height',
  batch_size: 'batch_size',
}

/**
 * 差し込む入力に添える値。`slot` は画像の枠の番号(1 始まり)。`input` はテンプレートの入力(値は伏せてあることがある)。
 */
export function boundInputValue(
  field: InputField,
  form: ViewFormValues,
  input: ComfyViewNodeInput | undefined,
  slot?: number,
): ShownValue {
  if (field === 'images') return { source: 'input', value: null, slot }
  if (field === 'mask') return { source: 'input', value: null }
  if (field === 'prompt') {
    return form.prompt.trim() !== '' ? { source: 'form', value: form.prompt } : templateOrRedacted(input)
  }
  const name = FIELD_PARAM[field]
  const formValue = name === undefined ? undefined : form.params[name]
  if (isFormValueSet(formValue)) return { source: 'form', value: formValue }
  if (field === 'seed') return { source: 'random', value: null }
  return templateOrRedacted(input)
}

/** 公開パラメーターに添える値(フォーム → 登録した既定値 → テンプレートの値)。 */
export function exposedParamValue(
  param: ComfyViewExposedParam,
  form: ViewFormValues,
  input: ComfyViewNodeInput | undefined,
): ShownValue {
  const formValue = form.params[param.name]
  if (isFormValueSet(formValue)) return { source: 'form', value: formValue }
  if (param.default_redacted) return { source: 'redacted', value: null }
  if (param.default !== null && param.default !== undefined) return { source: 'default', value: param.default }
  return templateOrRedacted(input)
}

/** 閲覧の応答の公開パラメーターを、図の部品が使う行(すべて有効)にする。 */
export function viewExposedRows(params: readonly ComfyViewExposedParam[]): ExposedParamRow[] {
  return params.map((p) => ({
    enabled: true,
    name: p.name,
    node: p.node,
    input: p.input,
    type: p.type,
    label: p.label,
    default: p.default_redacted ? null : (p.default ?? null),
    minimum: null,
    maximum: null,
    step: null,
    choices: null,
    maxLength: null,
  }))
}

/** 差し込み先と公開パラメーターの一覧(パネルの「差し込み先」に出す順)。 */
export type ViewEntry =
  | { kind: 'bound'; field: InputField; node: string; input: string; slot?: number }
  | { kind: 'node'; field: Extract<FieldKey, 'outputs' | 'final_prompt'>; node: string }
  | { kind: 'exposed'; param: ComfyViewExposedParam }

export function viewEntries(
  bindings: BindingsFormState,
  params: readonly ComfyViewExposedParam[],
): ViewEntry[] {
  const entries: ViewEntry[] = []
  const single: [InputField, BindingsFormState['prompt']][] = [
    ['prompt', bindings.prompt],
    ['negative_prompt', bindings.negativePrompt],
  ]
  for (const [field, ref] of single) if (ref) entries.push({ kind: 'bound', field, node: ref.node, input: ref.input })
  for (const ref of bindings.seeds) entries.push({ kind: 'bound', field: 'seed', node: ref.node, input: ref.input })
  const size: [InputField, BindingsFormState['prompt']][] = [
    ['width', bindings.width],
    ['height', bindings.height],
    ['batch_size', bindings.batchSize],
  ]
  for (const [field, ref] of size) if (ref) entries.push({ kind: 'bound', field, node: ref.node, input: ref.input })
  bindings.images.forEach((ref, i) =>
    entries.push({ kind: 'bound', field: 'images', node: ref.node, input: ref.input, slot: i + 1 }),
  )
  if (bindings.maskMode === 'load_image_mask' && bindings.maskRef) {
    entries.push({ kind: 'bound', field: 'mask', node: bindings.maskRef.node, input: bindings.maskRef.input })
  }
  for (const node of bindings.outputs) entries.push({ kind: 'node', field: 'outputs', node })
  if (bindings.finalPrompt) entries.push({ kind: 'node', field: 'final_prompt', node: bindings.finalPrompt })
  for (const param of params) entries.push({ kind: 'exposed', param })
  return entries
}

/** ノードの入力を名前で引く。 */
export function findViewInput(
  nodes: readonly ComfyViewNode[],
  nodeId: string,
  inputName: string,
): ComfyViewNodeInput | undefined {
  return nodes.find((n) => n.id === nodeId)?.inputs?.find((i) => i.name === inputName)
}

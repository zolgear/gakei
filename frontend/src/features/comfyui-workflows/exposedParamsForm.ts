/**
 * ワークフロー登録・編集フォームの「公開パラメーター」を扱う純粋関数(ADR-0013)。
 * analyze の `candidate_params` と、保存済みの `exposed_params` を1つの編集可能な表
 * (`ExposedParamRow[]`)にまとめ、保存前の最低限の検証も行う。
 */
import type { ComfyExposedParam, ComfyNodeInfo, ComfyParamType } from '../../api/client'
import { fmt, msg } from '../../i18n'
import { refExistsInNodes } from './nodeOptions'

export interface ExposedParamRow {
  /** チェックが付いている行だけ保存対象になる(既定は非公開)。 */
  enabled: boolean
  name: string
  node: string
  input: string
  type: ComfyParamType
  label: string
  default: unknown
  minimum: number | null
  maximum: number | null
  step: number | null
  choices: string[] | null
  maxLength: number | null
}

function refKey(node: string, input: string): string {
  return `${node}\u0000${input}`
}

function rowFromApi(param: ComfyExposedParam, enabled: boolean): ExposedParamRow {
  return {
    enabled,
    name: param.name,
    node: param.node,
    input: param.input,
    type: param.type,
    label: param.label,
    default: param.default ?? null,
    minimum: param.minimum ?? null,
    maximum: param.maximum ?? null,
    step: param.step ?? null,
    choices: param.choices ?? null,
    maxLength: param.max_length ?? null,
  }
}

export function exposedParamRowToApi(row: ExposedParamRow): ComfyExposedParam {
  return {
    name: row.name,
    node: row.node,
    input: row.input,
    type: row.type,
    label: row.label,
    default: row.default ?? null,
    minimum: row.minimum,
    maximum: row.maximum,
    step: row.step,
    choices: row.type === 'enum' ? row.choices : null,
    max_length: row.type === 'text' ? row.maxLength : null,
  }
}

/**
 * analyze の候補(`candidates`)と保存済みの公開パラメーター(`saved`)から表の行を作る。
 * 同じ (node, input) の候補があれば保存済みの設定(名前・ラベル・型など)を使い、チェックを
 * 付ける。保存済みだが候補に無いもの(候補が除外した組み合わせ)は末尾にそのまま足す。
 */
export function buildExposedParamRows(
  candidates: ComfyExposedParam[],
  saved: ComfyExposedParam[],
): ExposedParamRow[] {
  const savedByKey = new Map(saved.map((p) => [refKey(p.node, p.input), p]))
  const seen = new Set<string>()
  const rows: ExposedParamRow[] = []

  for (const candidate of candidates) {
    const key = refKey(candidate.node, candidate.input)
    seen.add(key)
    const savedParam = savedByKey.get(key)
    rows.push(savedParam ? rowFromApi(savedParam, true) : rowFromApi(candidate, false))
  }
  for (const savedParam of saved) {
    const key = refKey(savedParam.node, savedParam.input)
    if (!seen.has(key)) rows.push(rowFromApi(savedParam, true))
  }
  return rows
}

export interface ExposedReconcileResult {
  next: ExposedParamRow[]
  /** 未設定に戻した(行ごと消えた)公開パラメーターの名前(通知文の組み立てに使う)。 */
  clearedNames: string[]
}

/**
 * テンプレートを新しいファイルに差し替えたとき、既存の行の (node, input) が新しいノード
 * 一覧にまだ存在するかを確かめる。無くなった行は削除し(公開していた行だけ通知の対象)、
 * 新しいテンプレートで新たに出てきた候補は非公開の行として追加する。
 */
export function reconcileExposedParamsOnReplace(
  rows: ExposedParamRow[],
  newCandidates: ComfyExposedParam[],
  nodes: ComfyNodeInfo[],
): ExposedReconcileResult {
  const clearedNames: string[] = []
  const kept = rows.filter((row) => {
    const stillExists = refExistsInNodes({ node: row.node, input: row.input }, nodes)
    if (!stillExists && row.enabled) clearedNames.push(row.name || `${row.node}/${row.input}`)
    return stillExists
  })
  const keptKeys = new Set(kept.map((row) => refKey(row.node, row.input)))
  const added = newCandidates
    .filter((candidate) => !keptKeys.has(refKey(candidate.node, candidate.input)))
    .map((candidate) => rowFromApi(candidate, false))
  return { next: [...kept, ...added], clearedNames }
}

// -- 表の入力欄で使う小さな変換 -------------------------------------------------

/** 数値欄(min/max/step)の生入力 → 値。空文字は「未指定」= null。 */
export function parseNumberInput(raw: string): number | null {
  if (raw.trim() === '') return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

/** default 欄の生入力 → 型に応じた値。空文字は「未指定」= null(テンプレートの値のまま)。 */
export function parseDefaultInput(type: ComfyParamType, raw: string): unknown {
  if (raw === '') return null
  if (type === 'int') {
    const n = Number(raw)
    return Number.isFinite(n) ? Math.trunc(n) : null
  }
  if (type === 'float') {
    const n = Number(raw)
    return Number.isFinite(n) ? n : null
  }
  return raw
}

export function formatDefaultInput(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value)
}

/** choices 欄(カンマ区切りのテキスト)→ 文字列配列。空要素は落とす。 */
export function parseChoicesInput(raw: string): string[] {
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

export function formatChoicesInput(choices: string[] | null): string {
  return (choices ?? []).join(', ')
}

// -- 保存前の検証(ADR-0013 の名前の規則と同じ) ------------------------------

export const EXPOSED_PARAM_NAME_RE = /^[a-z][a-z0-9_]*$/

export const RESERVED_PARAM_NAMES: ReadonlySet<string> = new Set([
  'prompt',
  'negative_prompt',
  'seed',
  'width',
  'height',
  'batch_size',
  'size',
  'n',
  'model',
  'operation',
])

export function validateExposedParamName(name: string): string | null {
  const t = msg().comfyui.exposedParamValidation
  if (!EXPOSED_PARAM_NAME_RE.test(name)) {
    return t.nameFormatInvalid
  }
  if (name.startsWith('comfyui_')) return t.nameReservedPrefix
  if (RESERVED_PARAM_NAMES.has(name)) return t.nameReservedWord
  return null
}

/** 公開する行(`enabled`)だけを検証する。非公開の行は保存に含めないので検証しない。 */
export function validateExposedParamRows(rows: ExposedParamRow[]): string[] {
  const errors: string[] = []
  const enabledRows = rows.filter((row) => row.enabled)
  const seenNames = new Set<string>()

  for (const row of enabledRows) {
    const nameError = validateExposedParamName(row.name)
    const rowLabel = row.label || row.name || `${row.node}/${row.input}`
    if (nameError) errors.push(`${rowLabel}: ${nameError}`)
    if (row.name !== '' && seenNames.has(row.name)) {
      errors.push(fmt(msg().comfyui.exposedParamValidation.nameDuplicate, { name: row.name }))
    }
    seenNames.add(row.name)
  }
  return errors
}

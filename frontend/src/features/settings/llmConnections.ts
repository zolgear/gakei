/**
 * 管理者設定「LLM の接続先」(`/settings/llm-connections`。ADR-0032、中身は ADR-0024 8章)の
 * 表示・検証の純粋関数。API 呼び出しと状態管理は `pages/LlmConnectionsSettingsPage.tsx` が行う。
 *
 * ADR-0031 2章の「接続」の型: 接続先はカードで並べ、追加・編集・キーの登録と削除はダイアログで
 * その場で送る(ページの保存は無い)。キーは一部も表示しない(設定済みかどうかだけ)。
 */
import type {
  LlmApiStyle,
  LlmConnectionCreateRequest,
  LlmConnectionFeature,
  LlmConnectionUpdateRequest,
  LlmConnectionView,
} from '../../api/client'

/** `backend/app/domain/llm_connections.py` と同じ値。 */
export const CONNECTION_NAME_MAX = 100
/** 追加できる接続先の数(組み込みの「OpenAI の設定」は数えない)。 */
export const CONNECTIONS_MAX = 50

/** 接続先の追加・編集のフォーム。キーは追加のときだけ使う(編集ではキーを別に設定・削除する)。 */
export interface ConnectionForm {
  name: string
  base_url: string
  api_style: LlmApiStyle
  api_key: string
}

export function emptyConnectionForm(): ConnectionForm {
  return { name: '', base_url: '', api_style: 'responses', api_key: '' }
}

export function connectionFormFromView(view: LlmConnectionView): ConnectionForm {
  return { name: view.name, base_url: view.base_url ?? '', api_style: view.api_style, api_key: '' }
}

export type ConnectionFormErrors = Partial<Record<'name' | 'base_url', true>>

export function isValidConnectionName(value: string): boolean {
  const trimmed = value.trim()
  return trimmed.length > 0 && trimmed.length <= CONNECTION_NAME_MAX
}

/**
 * Base URL の検証(サーバーの `normalize_base_url` と同じ規則。ADR-0017)。http / https で
 * ホストがあり、ユーザー情報・クエリー・フラグメントを含まないこと。
 */
export function isValidConnectionBaseUrl(value: string): boolean {
  const trimmed = value.trim()
  if (!trimmed) return false
  let url: URL
  try {
    url = new URL(trimmed)
  } catch {
    return false
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false
  if (!url.hostname) return false
  if (url.username || url.password) return false
  // `new URL` は空のクエリー(`?` だけ)を '' にするので、元の文字列でも確かめる。
  if (url.search || url.hash || trimmed.includes('?') || trimmed.includes('#')) return false
  return true
}

export function validateConnectionForm(form: ConnectionForm): ConnectionFormErrors {
  const errors: ConnectionFormErrors = {}
  if (!isValidConnectionName(form.name)) errors.name = true
  if (!isValidConnectionBaseUrl(form.base_url)) errors.base_url = true
  return errors
}

/** 追加の本文。キーは空なら送らない(キーなし)。 */
export function connectionCreateBody(form: ConnectionForm): LlmConnectionCreateRequest {
  const body: LlmConnectionCreateRequest = {
    name: form.name.trim(),
    base_url: form.base_url.trim(),
    api_style: form.api_style,
  }
  const key = form.api_key.trim()
  if (key) body.api_key = key
  return body
}

/**
 * 編集の差分(PATCH の本文)。Base URL はサーバーが末尾の `/` を取るので、比べるときも取る
 * (`…/v1/` と入れ直しただけで差分にしないため)。
 */
export function diffConnectionForm(
  form: ConnectionForm,
  view: LlmConnectionView,
): LlmConnectionUpdateRequest {
  const body: LlmConnectionUpdateRequest = {}
  const name = form.name.trim()
  if (name !== view.name) body.name = name
  const baseUrl = form.base_url.trim()
  if (baseUrl.replace(/\/+$/, '') !== (view.base_url ?? '').replace(/\/+$/, '')) body.base_url = baseUrl
  if (form.api_style !== view.api_style) body.api_style = form.api_style
  return body
}

/** 追加した接続先(組み込みを除く)がまだ上限に達していないか。 */
export function canAddConnection(connections: readonly Pick<LlmConnectionView, 'builtin'>[]): boolean {
  return connections.filter((c) => !c.builtin).length < CONNECTIONS_MAX
}

/** 削除できない理由。削除できるなら null。 */
export function connectionDeleteBlocker(
  view: Pick<LlmConnectionView, 'builtin' | 'used_by'>,
): 'builtin' | 'in_use' | null {
  if (view.builtin) return 'builtin'
  if (isConnectionInUse(view)) return 'in_use'
  return null
}

/** どこかの機能で使っているか(使っていれば削除できない)。 */
export function isConnectionInUse(view: Pick<LlmConnectionView, 'used_by'>): boolean {
  return (view.used_by ?? []).length > 0
}

/** その機能が使っている接続先か。 */
export function isUsedBy(view: Pick<LlmConnectionView, 'used_by'>, feature: LlmConnectionFeature): boolean {
  return (view.used_by ?? []).includes(feature)
}

/** 画面に出す名前。組み込みの接続先は画面の言語の名前にする(サーバーの言語で返るため)。 */
export function connectionDisplayName(
  view: Pick<LlmConnectionView, 'builtin' | 'name'>,
  builtinName: string,
): string {
  return view.builtin ? builtinName : view.name
}

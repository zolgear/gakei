/**
 * 管理者設定「Mod」(`/settings/mods`。ADR-0051 7章)の表示・検証の純粋関数。API 呼び出しと
 * 状態管理は `pages/ModsSettingsPage.tsx` と `mods/` の部品が行う。
 *
 * - Webhook と Python の Mod は、ADR-0031 2章の「接続」の型(カードとダイアログ。その場で送る)。
 *   署名の鍵と、Python の Mod の秘密の項目は値を出さず、設定済みかどうかだけを出す。
 * - 呼ぶ順番は「操作」(上下のボタンでその場で送る)。
 * - イベントの名前(`instance.name`)だけが「保存で反映」。
 */
import type {
  ModDeliveryView,
  ModEvent,
  ModFilters,
  ModKind,
  ModOnFailure,
  ModOrderItem,
  ModPurpose,
  ModSource,
  ModsResponse,
  ModWebhookCreateRequest,
  ModWebhookUpdateRequest,
  ModWebhookView,
  PythonModUpdateRequest,
  PythonModView,
} from '../../api/client'

/** `backend/app/mods/config.py` と同じ値。 */
export const MOD_NAME_MAX = 100
export const MOD_INSTANCE_NAME_MAX = 100
export const MOD_URL_MAX = 2000
export const MOD_SECRET_MAX = 1000
export const MOD_SETTING_VALUE_MAX = 4000
export const MOD_TIMEOUT_MIN_SECONDS = 1
export const MOD_TIMEOUT_MAX_SECONDS = 300
export const MOD_DEFAULT_TIMEOUT_SECONDS = 10
export const MOD_WEBHOOKS_MAX = 50

export const MOD_EVENTS: readonly ModEvent[] = ['asset.ingested', 'ai.before_send', 'run.finished']
export const MOD_PURPOSES: readonly ModPurpose[] = ['image_run', 'annotation', 'assistant', 'embedding']
export const MOD_SOURCES: readonly ModSource[] = [
  'upload',
  'url',
  'sketch',
  'mask',
  'composite',
  'generated',
  'import',
  'mcp',
]
/**
 * 絞り込みに出すプロバイダー(`target.provider` と Run の `provider` の値)。ここに無い値が保存されて
 * いれば、それも選択肢に足す(`providerChoices`)。
 */
export const MOD_KNOWN_PROVIDERS: readonly string[] = ['openai', 'openai_compatible', 'comfyui', 'sdwebui', 'infinity']

/** 「判定を待つ」が意味を持つイベント(ADR-0051 2章)。`ai.before_send` は常に待つ。 */
export function waitForVerdictApplies(events: readonly ModEvent[]): boolean {
  return events.includes('asset.ingested') || events.includes('run.finished')
}

/** 購読の共通の項目のフォーム(Webhook と Python の Mod で同じ)。 */
export interface ModCommonForm {
  enabled: boolean
  events: ModEvent[]
  purposes: ModPurpose[]
  sources: ModSource[]
  providers: string[]
  /** 秒の入力(文字列)。 */
  timeout: string
  on_failure: ModOnFailure
  wait_for_verdict: boolean
}

export interface WebhookForm extends ModCommonForm {
  name: string
  url: string
  /** 鍵の入力。編集では空なら差し替えない。 */
  secret: string
  send_images: boolean
}

export type WebhookFormErrors = Partial<Record<'name' | 'url' | 'secret' | 'timeout', true>>

function commonFromView(view: {
  enabled: boolean
  filters: ModFilters
  timeout_seconds: number
  on_failure: ModOnFailure
  wait_for_verdict: boolean
}): Omit<ModCommonForm, 'events'> {
  return {
    enabled: view.enabled,
    purposes: [...(view.filters.purposes ?? [])],
    sources: [...(view.filters.sources ?? [])],
    providers: [...(view.filters.providers ?? [])],
    timeout: String(view.timeout_seconds),
    on_failure: view.on_failure,
    wait_for_verdict: view.wait_for_verdict,
  }
}

export function emptyWebhookForm(): WebhookForm {
  return {
    name: '',
    url: '',
    secret: '',
    send_images: false,
    enabled: false,
    events: [],
    purposes: [],
    sources: [],
    providers: [],
    timeout: String(MOD_DEFAULT_TIMEOUT_SECONDS),
    on_failure: 'block',
    wait_for_verdict: false,
  }
}

export function webhookFormFromView(view: ModWebhookView): WebhookForm {
  return {
    ...commonFromView(view),
    name: view.name,
    url: view.url,
    secret: '',
    send_images: view.send_images,
    events: [...view.events],
  }
}

/** Webhook の URL(サーバーの `normalize_url` と同じ規則)。http / https でホストがあり、ユーザー情報とフラグメントを含まない。 */
export function isValidWebhookUrl(value: string): boolean {
  const trimmed = value.trim()
  if (!trimmed || trimmed.length > MOD_URL_MAX) return false
  let url: URL
  try {
    url = new URL(trimmed)
  } catch {
    return false
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false
  if (!url.hostname) return false
  if (url.username || url.password) return false
  if (url.hash || trimmed.includes('#')) return false
  return true
}

export function isValidModName(value: string): boolean {
  const trimmed = value.trim()
  return trimmed.length > 0 && trimmed.length <= MOD_NAME_MAX
}

/** 待つ時間(秒)の入力。小数も受ける。 */
export function isValidTimeoutInput(value: string): boolean {
  const trimmed = value.trim()
  if (!trimmed) return false
  const n = Number(trimmed)
  return Number.isFinite(n) && n >= MOD_TIMEOUT_MIN_SECONDS && n <= MOD_TIMEOUT_MAX_SECONDS
}

/** `isNew` のときは鍵が必須。編集では入れたときだけ確かめる。 */
export function validateWebhookForm(form: WebhookForm, isNew: boolean): WebhookFormErrors {
  const errors: WebhookFormErrors = {}
  if (!isValidModName(form.name)) errors.name = true
  if (!isValidWebhookUrl(form.url)) errors.url = true
  const secret = form.secret.trim()
  if ((isNew && !secret) || secret.length > MOD_SECRET_MAX) errors.secret = true
  if (!isValidTimeoutInput(form.timeout)) errors.timeout = true
  return errors
}

function filtersFromForm(form: ModCommonForm): ModFilters {
  return { purposes: [...form.purposes], sources: [...form.sources], providers: [...form.providers] }
}

/** 選択肢の順に並べ直す(チェックの順で差分が出ないように)。 */
function inOrder<T extends string>(values: readonly T[], order: readonly T[]): T[] {
  const known = order.filter((v) => values.includes(v))
  const unknown = values.filter((v) => !order.includes(v))
  return [...known, ...unknown]
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false
  const sb = new Set(b)
  return a.every((v) => sb.has(v))
}

function sameFilters(a: ModFilters, b: ModFilters): boolean {
  return (
    sameSet(a.purposes ?? [], b.purposes ?? []) &&
    sameSet(a.sources ?? [], b.sources ?? []) &&
    sameSet(a.providers ?? [], b.providers ?? [])
  )
}

export function webhookCreateBody(form: WebhookForm): ModWebhookCreateRequest {
  return {
    name: form.name.trim(),
    url: form.url.trim(),
    secret: form.secret.trim(),
    enabled: form.enabled,
    events: inOrder(form.events, MOD_EVENTS),
    filters: filtersFromForm(form),
    send_images: form.send_images,
    timeout_seconds: Number(form.timeout.trim()),
    on_failure: form.on_failure,
    wait_for_verdict: form.wait_for_verdict,
  }
}

/** 編集の差分(PATCH の本文)。鍵は入れたときだけ送る。 */
export function diffWebhookForm(form: WebhookForm, view: ModWebhookView): ModWebhookUpdateRequest {
  const body: ModWebhookUpdateRequest = {}
  const name = form.name.trim()
  if (name !== view.name) body.name = name
  const url = form.url.trim()
  if (url !== view.url) body.url = url
  const secret = form.secret.trim()
  if (secret) body.secret = secret
  if (form.enabled !== view.enabled) body.enabled = form.enabled
  if (!sameSet(form.events, view.events)) body.events = inOrder(form.events, MOD_EVENTS)
  const filters = filtersFromForm(form)
  if (!sameFilters(filters, view.filters)) body.filters = filters
  if (form.send_images !== view.send_images) body.send_images = form.send_images
  const timeout = Number(form.timeout.trim())
  if (timeout !== view.timeout_seconds) body.timeout_seconds = timeout
  if (form.on_failure !== view.on_failure) body.on_failure = form.on_failure
  if (form.wait_for_verdict !== view.wait_for_verdict) body.wait_for_verdict = form.wait_for_verdict
  return body
}

export function canAddWebhook(webhooks: readonly unknown[]): boolean {
  return webhooks.length < MOD_WEBHOOKS_MAX
}

// -- Python の Mod -----------------------------------------------------------------

export type PythonModState = 'error' | 'enabled' | 'disabled'

/** カードに出す状態。読み込みエラーは有効・無効より先に出す。 */
export function pythonModState(view: Pick<PythonModView, 'error' | 'enabled'>): PythonModState {
  if (view.error) return 'error'
  return view.enabled ? 'enabled' : 'disabled'
}

/** マニフェストが読めたか(読めなければ設定の項目もイベントも分からないので、設定を開かせない)。 */
export function hasManifest(view: Pick<PythonModView, 'name'>): boolean {
  return view.name !== null && view.name !== undefined
}

/** 購読しているイベント。`events` が null ならマニフェストのイベントすべて。 */
export function pythonModEvents(view: Pick<PythonModView, 'events' | 'available_events'>): ModEvent[] {
  return view.events ? [...view.events] : [...(view.available_events ?? [])]
}

export interface PythonModForm extends ModCommonForm {
  /** 秘密でない項目の値。数値は入力の文字列のまま持つ。 */
  values: Record<string, string | boolean>
  /** 秘密の項目の新しい値(空なら変えない)。 */
  secrets: Record<string, string>
  /** 消す秘密の項目。 */
  clearSecrets: string[]
}

function settingValueToForm(type: string, value: unknown): string | boolean {
  if (type === 'bool') return value === true
  if (value === null || value === undefined) return ''
  return String(value)
}

export function pythonModFormFromView(view: PythonModView): PythonModForm {
  const values: Record<string, string | boolean> = {}
  for (const field of view.settings ?? []) {
    if (field.type === 'secret') continue
    values[field.key] = settingValueToForm(field.type, field.value)
  }
  return {
    ...commonFromView(view),
    events: pythonModEvents(view),
    values,
    secrets: {},
    clearSecrets: [],
  }
}

export type PythonModFormErrors = Partial<Record<string, true>> & { timeout?: true }

/** 設定の項目の検証。エラーのキーは項目の `key`(待つ時間は `timeout`)。 */
export function validatePythonModForm(form: PythonModForm, view: PythonModView): PythonModFormErrors {
  const errors: PythonModFormErrors = {}
  if (!isValidTimeoutInput(form.timeout)) errors.timeout = true
  for (const field of view.settings ?? []) {
    const value = form.values[field.key]
    if (field.type === 'number') {
      const text = typeof value === 'string' ? value.trim() : ''
      if (text !== '' && !Number.isFinite(Number(text))) errors[field.key] = true
    } else if (field.type === 'string' || field.type === 'path') {
      if (typeof value === 'string' && value.length > MOD_SETTING_VALUE_MAX) errors[field.key] = true
    } else if (field.type === 'secret') {
      const secret = (form.secrets[field.key] ?? '').trim()
      if (secret.length > MOD_SECRET_MAX) errors[field.key] = true
    }
  }
  return errors
}

/** フォームの値を API の値にする。数値の空欄は null(既定に戻す)。 */
function settingValueFromForm(type: string, value: string | boolean | undefined): unknown {
  if (type === 'bool') return value === true
  const text = typeof value === 'string' ? value : ''
  if (type === 'number') return text.trim() === '' ? null : Number(text.trim())
  return text
}

function sameSettingValue(a: unknown, b: unknown): boolean {
  return Object.is(a ?? null, b ?? null)
}

/**
 * 編集の差分(PATCH の本文)。イベントはマニフェストのイベントすべてを選んだら null(この先
 * マニフェストに足されたイベントも受ける)。値は変えた項目だけ、秘密は入れた・消したものだけ送る。
 */
export function diffPythonModForm(form: PythonModForm, view: PythonModView): PythonModUpdateRequest {
  const body: PythonModUpdateRequest = {}
  if (form.enabled !== view.enabled) body.enabled = form.enabled
  const available = view.available_events ?? []
  if (!sameSet(form.events, pythonModEvents(view))) {
    body.events = sameSet(form.events, available) ? null : inOrder(form.events, MOD_EVENTS)
  }
  const filters = filtersFromForm(form)
  if (!sameFilters(filters, view.filters)) body.filters = filters
  const timeout = Number(form.timeout.trim())
  if (timeout !== view.timeout_seconds) body.timeout_seconds = timeout
  if (form.on_failure !== view.on_failure) body.on_failure = form.on_failure
  if (form.wait_for_verdict !== view.wait_for_verdict) body.wait_for_verdict = form.wait_for_verdict

  const values: Record<string, unknown> = {}
  const secrets: Record<string, string | null> = {}
  for (const field of view.settings ?? []) {
    if (field.type === 'secret') {
      const secret = (form.secrets[field.key] ?? '').trim()
      if (secret) secrets[field.key] = secret
      else if (form.clearSecrets.includes(field.key) && field.secret_set) secrets[field.key] = null
      continue
    }
    const next = settingValueFromForm(field.type, form.values[field.key])
    const current = field.type === 'bool' ? field.value === true : (field.value ?? null)
    if (!sameSettingValue(next, current)) values[field.key] = next
  }
  if (Object.keys(values).length > 0) body.values = values
  if (Object.keys(secrets).length > 0) body.secrets = secrets
  return body
}

// -- 共通 ---------------------------------------------------------------------------

/** 絞り込みのプロバイダーの選択肢。保存済みの知らない値も残す。 */
export function providerChoices(selected: readonly string[]): string[] {
  return [...MOD_KNOWN_PROVIDERS, ...selected.filter((p) => !MOD_KNOWN_PROVIDERS.includes(p))]
}

/** チェックボックスの切り替え(選択肢の順を保つ)。 */
export function toggleChoice<T extends string>(values: readonly T[], value: T, checked: boolean, order: readonly T[]): T[] {
  const next = checked ? (values.includes(value) ? [...values] : [...values, value]) : values.filter((v) => v !== value)
  return inOrder(next, order)
}

/** イベント名(`.` を含む)を、辞書のキーに直す。 */
export function eventKey(event: ModEvent): 'assetIngested' | 'aiBeforeSend' | 'runFinished' {
  if (event === 'asset.ingested') return 'assetIngested'
  if (event === 'ai.before_send') return 'aiBeforeSend'
  return 'runFinished'
}

/** プロバイダーの表示名。辞書に無いものは値のまま。 */
export function providerLabel(provider: string, labels: Record<string, string>): string {
  return labels[provider] ?? provider
}

/** 差分が空か。 */
export function isEmptyPatch(body: object): boolean {
  return Object.keys(body).length === 0
}

export interface OrderedMod {
  kind: ModKind
  id: string
  name: string
  enabled: boolean
  order: number | null
}

/**
 * 呼ぶ順に並べた Mod(Webhook と Python の Mod)。順番の無いもの(まだ有効にしていない Python の Mod
 * など)は後ろに名前の順で並べる。マニフェストの読めない Python の Mod は並べない。
 */
export function orderedMods(data: Pick<ModsResponse, 'webhooks' | 'python_mods'>): OrderedMod[] {
  const items: OrderedMod[] = [
    ...data.webhooks.map((w) => ({
      kind: 'webhook' as const,
      id: w.id,
      name: w.name,
      enabled: w.enabled,
      order: w.order ?? null,
    })),
    ...data.python_mods
      .filter((p) => hasManifest(p))
      .map((p) => ({
        kind: 'python' as const,
        id: p.id,
        name: p.name ?? p.id,
        enabled: p.enabled,
        order: p.order ?? null,
      })),
  ]
  return items.sort((a, b) => {
    if (a.order !== null && b.order !== null && a.order !== b.order) return a.order - b.order
    if (a.order !== null && b.order === null) return -1
    if (a.order === null && b.order !== null) return 1
    return a.name.localeCompare(b.name)
  })
}

/** `index` の Mod を `delta`(-1 で上、+1 で下)動かした並び。動かせなければ null。 */
export function moveMod(items: readonly OrderedMod[], index: number, delta: -1 | 1): ModOrderItem[] | null {
  const target = index + delta
  if (index < 0 || index >= items.length || target < 0 || target >= items.length) return null
  const next = [...items]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  return next.map((m) => ({ kind: m.kind, id: m.id }))
}

/** 配達の一覧に出す Mod の名前(一覧に無い・消した Mod は id のまま)。 */
export function modNameFor(
  data: Pick<ModsResponse, 'webhooks' | 'python_mods'> | undefined,
  delivery: Pick<ModDeliveryView, 'mod_kind' | 'mod_id'>,
): string {
  if (!data) return delivery.mod_id
  if (delivery.mod_kind === 'webhook') {
    return data.webhooks.find((w) => w.id === delivery.mod_id)?.name ?? delivery.mod_id
  }
  return data.python_mods.find((p) => p.id === delivery.mod_id)?.name ?? delivery.mod_id
}

/** 所要時間の表示(1秒未満はミリ秒、それ以上は秒を小数1桁)。 */
export function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

/** テスト送信の応答の本文を読める形にする(JSON は字下げ、無ければ null)。 */
export function formatTestResponse(response: unknown): string | null {
  if (response === null || response === undefined) return null
  if (typeof response === 'string') return response
  try {
    return JSON.stringify(response, null, 2)
  } catch {
    return String(response)
  }
}

/** イベントの名前(`instance.name`)の入力の検証。空は「名前なし」。 */
export function isValidInstanceName(value: string): boolean {
  return value.trim().length <= MOD_INSTANCE_NAME_MAX
}

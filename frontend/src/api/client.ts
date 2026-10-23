/**
 * backend への薄い fetch ラッパー。openapi-typescript が生成した型(`./schema`)をそのまま使う。
 * エラー時は FastAPI の `detail`(文字列 or ValidationError[])を取り出して ApiError として投げる。
 */
import type { components, operations } from './schema'
import { getLocale } from '../i18n/locale'
import { markUnauthenticated } from '../features/auth/authState'

// -- 認証(ADR-0019) ----------------------------------------------------------

export type AuthMeResponse = components['schemas']['AuthMeResponse']
export type AuthUser = components['schemas']['AuthUser']
export type AuthLogoutResponse = components['schemas']['AuthLogoutResponse']
export type CreatedBy = components['schemas']['CreatedBy']
/** 切り出す範囲(元画像のピクセル座標。ADR-0020 5章)。`features/crop/cropMath.ts` の `CropRect` と同じ形。 */
export type CropRect = components['schemas']['CropRect']

/** バージョンと commit(ADR-0021 3章)。 */
export type AboutResponse = components['schemas']['AboutResponse']

export type CapabilitiesResponse = components['schemas']['CapabilitiesResponse']
export type ProviderEntry = components['schemas']['ProviderEntry']
export type ModelCapabilities = components['schemas']['ModelCapabilities']
export type OperationCapabilities = components['schemas']['OperationCapabilities']
export type ParamDef = components['schemas']['ParamDef']
export type IncompatiblePair = components['schemas']['IncompatiblePair']
export type ConditionalParam = components['schemas']['ConditionalParam']
export type SizeConstraints = components['schemas']['SizeConstraints']
export type OutputSizeRule = components['schemas']['OutputSizeRule']
export type OutputDimension = components['schemas']['OutputDimension']
export type OutputSizeOperand = components['schemas']['OutputSizeOperand']

export type RunSummary = components['schemas']['RunSummary']
export type RunDetail = components['schemas']['RunDetail']
export type RunCreateRequest = components['schemas']['RunCreateRequest']
export type RunCreateResponse = components['schemas']['RunCreateResponse']
export type RunListResponse = components['schemas']['RunListResponse']
export type RunInputCreate = components['schemas']['RunInputCreate']
export type RunEvent = components['schemas']['RunEvent']
export type RunOutputRef = components['schemas']['RunOutputRef']
export type RunTextOutput = components['schemas']['RunTextOutput']
export type RunStatus = RunSummary['status']
// 領域の指示(ADR-0053)。
export type RegionBox = components['schemas']['RegionBox']
export type RegionKind = RegionBox['kind']
export type RunRegions = components['schemas']['RunRegions']
export type DetectedRegion = components['schemas']['DetectedRegion']
export type RegionDetectResponse = components['schemas']['RegionDetectResponse']
export type RegionDetectionSettingsResponse = components['schemas']['RegionDetectionSettingsResponse']
export type RegionDetectionSettingsUpdateRequest = components['schemas']['RegionDetectionSettingsUpdateRequest']
/** 保存した検出1回分(Asset ごとの履歴)。 */
export type RegionDetectionRecord = components['schemas']['RegionDetectionView']
export type RegionDetectionListResponse = components['schemas']['RegionDetectionListResponse']

export type AssetDetail = components['schemas']['AssetDetail']
export type AssetSummary = components['schemas']['AssetSummary']
export type AssetOrigin = components['schemas']['AssetOrigin']
export type EmbeddedGenerationMeta = components['schemas']['EmbeddedGenerationMeta']
export type AssetUploadResponse = components['schemas']['AssetUploadResponse']

export type AssetLineageResponse = components['schemas']['AssetLineageResponse']
export type LineageNode = components['schemas']['LineageNode']
export type LineageEdge = components['schemas']['LineageEdge']
export type LineageAssetInfo = components['schemas']['LineageAssetInfo']
export type LineageRunInfo = components['schemas']['LineageRunInfo']

// -- グループ(ADR-0022) ------------------------------------------------------

export type AssetGroupRow = components['schemas']['AssetGroupRow']
export type AssetGroupRef = components['schemas']['AssetGroupRef']
export type AssetGroupListResponse = components['schemas']['AssetGroupListResponse']

export type PromptSetResponse = components['schemas']['PromptSetResponse']
export type PromptSetItemResponse = components['schemas']['PromptSetItemResponse']
export type PromptSetListResponse = components['schemas']['PromptSetListResponse']
export type PromptSetCreateRequest = components['schemas']['PromptSetCreateRequest']
export type PromptSetUpdateRequest = components['schemas']['PromptSetUpdateRequest']
export type PromptSetItemAppendRequest = components['schemas']['PromptSetItemAppendRequest']
export type PromptSetItemUpdateRequest = components['schemas']['PromptSetItemUpdateRequest']
export type ParameterSetResponse = components['schemas']['ParameterSetResponse']
export type ParameterSetListResponse = components['schemas']['ParameterSetListResponse']
export type ParameterSetCreateRequest = components['schemas']['ParameterSetCreateRequest']
export type ParameterSetUpdateRequest = components['schemas']['ParameterSetUpdateRequest']

// -- プロンプトテンプレート(実験) ------------------------------------------

export type PromptTemplateSummary = components['schemas']['PromptTemplateSummary']
export type PromptTemplateListResponse = components['schemas']['PromptTemplateListResponse']
export type PromptTemplateResponse = components['schemas']['PromptTemplateResponse']
export type PromptTemplateWriteRequest = components['schemas']['PromptTemplateWriteRequest']
export type PromptSlotResponse = components['schemas']['PromptSlotResponse']
export type PromptVariantResponse = components['schemas']['PromptVariantResponse']
export type PromptComboResponse = components['schemas']['PromptComboResponse']
export type PromptSlotInput = components['schemas']['PromptSlotInput']
export type PromptVariantInput = components['schemas']['PromptVariantInput']
export type PromptComboInput = components['schemas']['PromptComboInput']
export type PromptSelections = components['schemas']['PromptSelections']
export type PromptSlotSelection = components['schemas']['PromptSlotSelection']
export type PromptBuildRequest = components['schemas']['PromptBuildRequest']
export type PromptBuildResponse = components['schemas']['PromptBuildResponse']
export type RunPromptTemplate = components['schemas']['RunPromptTemplate']

export type SearchResponse = components['schemas']['SearchResponse']
export type SearchRunHit = components['schemas']['SearchRunHit']
export type SearchAssetHit = components['schemas']['SearchAssetHit']
export type SearchPromptSetHit = components['schemas']['SearchPromptSetHit']
export type SearchPromptSetItemHit = components['schemas']['SearchPromptSetItemHit']

export type OpenAIKeyStatus = components['schemas']['OpenAIKeyStatusResponse']
export type OpenAIBaseUrlStatus = components['schemas']['OpenAIBaseUrlStatusResponse']

// -- 全般設定(moderation、ComfyUI タイムアウト。ADR-0009、ADR-0013 7章) -------------

export type GeneralSettingsResponse = components['schemas']['GeneralSettingsResponse']
export type GeneralSettingsUpdateRequest = components['schemas']['GeneralSettingsUpdateRequest']
export type ModerationSetting = components['schemas']['ModerationSetting']
export type ComfyUITimeoutSetting = components['schemas']['ComfyUITimeoutSetting']

// -- タイトルとタグ(ADR-0024) --------------------------------------------------

export type AssetTagRef = components['schemas']['AssetTagRef']
export type TagSource = AssetTagRef['source']
export type AnnotationStatusView = components['schemas']['AnnotationStatusView']
export type AssetAnnotationResponse = components['schemas']['AssetAnnotationResponse']
export type TagCount = components['schemas']['TagCount']
export type TagListResponse = components['schemas']['TagListResponse']
export type TagSuggestion = components['schemas']['TagSuggestion']
export type TagSuggestionResponse = components['schemas']['TagSuggestionResponse']
export type PromptTagsResponse = components['schemas']['PromptTagsResponse']
export type TagDictionaryItem = components['schemas']['TagDictionaryItem']
export type TagDictionaryListResponse = components['schemas']['TagDictionaryListResponse']
export type TagDictionaryUpdateRequest = components['schemas']['TagDictionaryUpdateRequest']
export type TagCategoryScheme = NonNullable<TagDictionaryUpdateRequest['category_scheme']>
export type AnnotationSettingsResponse = components['schemas']['AnnotationSettingsResponse']
export type AnnotationSettingsUpdateRequest = components['schemas']['AnnotationSettingsUpdateRequest']
export type AnnotationBackfillResponse = components['schemas']['AnnotationBackfillResponse']
export type AnnotationConnectionCalls = components['schemas']['AnnotationConnectionCalls']
export type AnnotationTarget = components['schemas']['AnnotationTarget']
export type AnnotationProfiles = components['schemas']['AnnotationProfiles']
export type AnnotationProfilesUpdate = components['schemas']['AnnotationProfilesUpdate']
export type OnnxModelStatus = components['schemas']['OnnxModelStatus']
export type OnnxModelName = OnnxModelStatus['name']
export type AnnotationEngine = NonNullable<AnnotationSettingsResponse['usable_engines']>[number]

// -- レーティング(ADR-0049) ---------------------------------------------------

/** 段階の識別子(`g`、`r`、`x`、`xx`、`xxx`)。未判定は null。 */
export type ContentRatingLevel = NonNullable<AssetSummary['content_rating']>
export type ContentRatingDetail = components['schemas']['ContentRatingDetail']
export type ContentRatingSignal = components['schemas']['ContentRatingSignal']
export type AssetContentRatingResponse = components['schemas']['AssetContentRatingResponse']

// -- ユーザーごとの設定(ADR-0049 6章) -----------------------------------------

export type UserPreferencesResponse = components['schemas']['UserPreferencesResponse']
export type UserPreferencesUpdateRequest = components['schemas']['UserPreferencesUpdateRequest']

// -- LLM の接続先(ADR-0032) ----------------------------------------------------
export type LlmConnectionsResponse = components['schemas']['LlmConnectionsResponse']
export type LlmConnectionView = components['schemas']['LlmConnectionView']
export type LlmConnectionCreateRequest = components['schemas']['LlmConnectionCreateRequest']
export type LlmConnectionUpdateRequest = components['schemas']['LlmConnectionUpdateRequest']
export type LlmApiStyle = LlmConnectionView['api_style']
/** 接続先の構造化出力(JSON のスキーマ)への対応の指定。`auto` は OpenAI の公式だけを対応とみなす。 */
export type StructuredOutputsMode = LlmConnectionView['structured_outputs']
export type LlmConnectionFeature = NonNullable<LlmConnectionView['used_by']>[number]

// -- MCP サーバーとアクセストークン(ADR-0023) ----------------------------------

export type McpSettingsResponse = components['schemas']['McpSettingsResponse']
export type McpSettingsUpdateRequest = components['schemas']['McpSettingsUpdateRequest']
export type ApiTokenRow = components['schemas']['ApiTokenRow']
export type ApiTokenListResponse = components['schemas']['ApiTokenListResponse']
export type ApiTokenCreateResponse = components['schemas']['ApiTokenCreateResponse']
export type ApiTokenCreateRequest = components['schemas']['ApiTokenCreateRequest']

// -- MCP サーバー(ADR-0023) ---------------------------------------------------

/** 全ログイン者が読める。`runs_last_hour` は直近1時間に MCP 経由で作られた Run の数。 */
export function getMcpSettings(): Promise<McpSettingsResponse> {
  return request('/api/settings/mcp')
}

/** 管理者のみ。省略した項目は変更しない。範囲外の上限は 422(`ApiError.message` に detail)。 */
export function updateMcpSettings(body: McpSettingsUpdateRequest): Promise<McpSettingsResponse> {
  return request('/api/settings/mcp', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// -- アクセストークン(ADR-0023、oidc モードだけ) --------------------------------
// none モードでは3つとも 404(`t("auth.disabled")`)。

export function listApiTokens(): Promise<ApiTokenListResponse> {
  return request('/api/users/me/api-tokens')
}

/**
 * 発行する。トークンの値(`token`)はこの応答にだけ載り、以降は取得できない。
 * 有効期限(`expires_in_days`。null は無期限)と権限(`scope`)は発行のときだけ選べる(ADR-0023 11章)。
 */
export function createApiToken(body: ApiTokenCreateRequest): Promise<ApiTokenCreateResponse> {
  return request('/api/users/me/api-tokens', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function revokeApiToken(tokenId: string): Promise<void> {
  return request(`/api/users/me/api-tokens/${tokenId}`, { method: 'DELETE' })
}

// -- 認証の設定(ADR-0034。管理者のみ) -------------------------------------------

export type AuthSettingsResponse = components['schemas']['AuthSettingsResponse']
export type AuthSettingsUpdateRequest = components['schemas']['AuthSettingsUpdateRequest']
export type AuthConnectionRequest = components['schemas']['AuthConnectionRequest']
export type AuthConnectionView = components['schemas']['AuthConnectionView']
export type AuthPendingConnectionView = components['schemas']['AuthPendingConnectionView']
export type AuthEnableBlocker = AuthSettingsResponse['enable_blockers'][number]
export type AuthSettingSource = AuthSettingsResponse['admin_emails']['source']

export function getAuthSettings(): Promise<AuthSettingsResponse> {
  return request('/api/settings/auth')
}

/**
 * 接続の仮登録(形式の検査と Discovery 文書の取得まで)。`client_secret` は省略で引き継ぎ、
 * 空文字で public client、値で差し替え。テストログインに成功するまで実効の設定は変わらない。
 */
export function setAuthConnection(body: AuthConnectionRequest): Promise<AuthSettingsResponse> {
  return request('/api/settings/auth/connection', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function discardAuthPendingConnection(): Promise<AuthSettingsResponse> {
  return request('/api/settings/auth/connection/pending', { method: 'DELETE' })
}

/** 省略した項目は変えない。null は保存済みの値を消して .env・既定に戻す。 */
export function updateAuthSettings(body: AuthSettingsUpdateRequest): Promise<AuthSettingsResponse> {
  return request('/api/settings/auth', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// -- 共有リンク(ADR-0029) -------------------------------------------------------

export type ShareScope = components['schemas']['SharePreviewRequest']['scope']
export type ShareSettingsResponse = components['schemas']['ShareSettingsResponse']
export type SharePreviewResponse = components['schemas']['SharePreviewResponse']
export type SharePreviewAsset = components['schemas']['SharePreviewAsset']
export type ShareCreateRequest = components['schemas']['ShareCreateRequest']
export type ShareRow = components['schemas']['ShareRow']
export type ShareListResponse = components['schemas']['ShareListResponse']
export type PublicShareResponse = components['schemas']['PublicShareResponse']
export type PublicShareAsset = components['schemas']['PublicShareAsset']
export type PublicShareRun = components['schemas']['PublicShareRun']
export type PublicShareEdge = components['schemas']['PublicShareEdge']

/** 全ログイン者が読める(共有の操作を画面に出すかの判断)。既定は無効。 */
export function getShareSettings(): Promise<ShareSettingsResponse> {
  return request('/api/settings/share')
}

/** 管理者のみ。 */
export function updateShareSettings(enabled: boolean): Promise<ShareSettingsResponse> {
  return request('/api/settings/share', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
}

/** 作る前の確認(範囲に含まれる画像)。何も書き込まない。機能が無効なら 409。 */
export function previewShare(assetId: string, scope: ShareScope): Promise<SharePreviewResponse> {
  return request('/api/shares/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_id: assetId, scope }),
  })
}

export function createShare(body: ShareCreateRequest): Promise<ShareRow> {
  return request('/api/shares', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** 自分の、取り消していない共有(新しい順)。 */
export function listShares(): Promise<ShareListResponse> {
  return request('/api/shares')
}

/** 取り消す(元に戻せない)。 */
export function revokeShare(shareId: string): Promise<void> {
  return request(`/api/shares/${shareId}`, { method: 'DELETE' })
}

/** 共有のページの内容(ログイン不要)。無効・取り消し済み・不明はどれも 404。 */
export function getPublicShare(token: string): Promise<PublicShareResponse> {
  return request(`/api/public/shares/${encodeURIComponent(token)}`)
}

// -- 系列の持ち出しと取り込み(ADR-0037) ---------------------------------------

export type LineageExportPreviewResponse = components['schemas']['LineageExportPreviewResponse']
export type LineageExportScope = LineageExportPreviewResponse['scope']
export type LineageImportResponse = components['schemas']['LineageImportResponse']
export type RunImportInfo = components['schemas']['RunImportInfo']

/**
 * 書き出す前の確認(範囲に含まれる画像と Run の数、原本の合計)。何も書き込まない。
 * `includeGraph` なら、ZIP に入る Asset と Run だけの系列グラフ(`graph`)も受け取る。
 */
export function previewLineageExport(
  assetId: string,
  scope: LineageExportScope,
  options: { includeGraph?: boolean } = {},
): Promise<LineageExportPreviewResponse> {
  const query = new URLSearchParams({ scope })
  if (options.includeGraph) query.set('include_graph', 'true')
  return request(`/api/assets/${assetId}/export/preview?${query}`)
}

export type LineageExportMode = NonNullable<
  NonNullable<operations['export_lineage']['parameters']['query']>['mode']
>

export interface LineageExportOptions {
  scope: LineageExportScope
  /** `import`(GAKEI に取り込む)/ `delivery`(納品用。index.html と README.txt を加える)。 */
  mode: LineageExportMode
  /** 実行者の表示名を含める(既定は含めない。ADR-0037 4章)。 */
  includeCreatorNames: boolean
  /** 納品用の index.html の言語(画面の表示言語)。 */
  lang: 'ja' | 'en'
  /** 納品用の index.html の日時のタイムゾーン(IANA。分からなければ省く)。 */
  timeZone?: string
}

/** 書き出しの URL の query。取り込み用のときは index.html の言語・タイムゾーンを送らない。 */
export function lineageExportQuery(options: LineageExportOptions): URLSearchParams {
  const query = new URLSearchParams({ scope: options.scope, mode: options.mode })
  if (options.includeCreatorNames) query.set('include_creator_names', 'true')
  if (options.mode === 'delivery') {
    query.set('lang', options.lang)
    if (options.timeZone) query.set('tz', options.timeZone)
  }
  return query
}

/** 系列の ZIP をダウンロードする URL(`<a href download>` に渡す。Cookie の認証がそのまま効く)。 */
export function lineageExportUrl(
  assetId: string,
  options: Omit<LineageExportOptions, 'lang' | 'timeZone'>,
): string {
  let timeZone: string | undefined
  try {
    timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || undefined
  } catch {
    timeZone = undefined
  }
  const query = lineageExportQuery({ ...options, lang: getLocale(), timeZone })
  return `/api/assets/${assetId}/export?${query}`
}

/**
 * 系列の ZIP を取り込む。大きなファイルを送るので、送信の進み具合(0〜1)を `onProgress` で
 * 知らせる(fetch では取れないため XMLHttpRequest を使う)。エラーは `request` と同じく
 * `ApiError`(サーバーの `detail` の文言)にする。
 */
export function importLineage(
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<LineageImportResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/imports/lineage')
    xhr.setRequestHeader('Accept-Language', getLocale())
    xhr.responseType = 'text'
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && e.total > 0) onProgress(Math.min(1, e.loaded / e.total))
      }
    }
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        body = null
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as LineageImportResponse)
        return
      }
      if (xhr.status === 401) markUnauthenticated()
      const detail = body && typeof body === 'object' && 'detail' in body ? (body as { detail: unknown }).detail : null
      reject(new ApiError(xhr.status, formatDetail(detail) ?? (xhr.statusText || `HTTP ${xhr.status}`)))
    }
    // 通信の失敗は ApiError にしない(画面は自分の文言を出す)。
    xhr.onerror = () => reject(new Error('network error'))
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

// -- ComfyUI ワークフロー(ADR-0013) ------------------------------------------

export type ComfyUIStatus = components['schemas']['ComfyUIStatusResponse']
export type ComfyUIConnectionTestResponse = components['schemas']['ComfyUIConnectionTestResponse']
export type ComfyAnalyzeResponse = components['schemas']['ComfyAnalyzeResponse']
export type ComfyNodeInfo = components['schemas']['ComfyNodeInfo']
export type ComfyNodeInputInfo = components['schemas']['ComfyNodeInputInfo']
export type ComfyWorkflowSummary = components['schemas']['ComfyWorkflowSummary']
export type ComfyWorkflowDetail = components['schemas']['ComfyWorkflowDetail']
export type ComfyWorkflowListResponse = components['schemas']['ComfyWorkflowListResponse']
export type ComfyWorkflowCreateRequest = components['schemas']['ComfyWorkflowCreateRequest']
export type ComfyWorkflowUpdateRequest = components['schemas']['ComfyWorkflowUpdateRequest']
export type ComfyBindings = components['schemas']['Bindings']
export type ComfySuggestedBindings = components['schemas']['SuggestedBindings']
export type ComfyInputRef = components['schemas']['InputRef']
export type ComfyMaskBinding = components['schemas']['MaskBinding']
export type ComfyExposedParam = components['schemas']['ExposedParam']
export type ComfyOperation = ComfyWorkflowSummary['operation']
export type ComfyBindingReading = components['schemas']['BindingReading']
export type ComfyBindingCandidate = components['schemas']['BindingCandidate']
export type ComfyPathStep = components['schemas']['PathStep']
export type ComfyAnalyzeEdge = components['schemas']['AnalyzeEdge']
export type ComfyNodeKind = NonNullable<ComfyNodeInfo['kind']>
export type ComfyWorkflowView = components['schemas']['ComfyWorkflowViewResponse']
export type ComfyViewNode = components['schemas']['ComfyViewNode']
export type ComfyViewNodeInput = components['schemas']['ComfyViewNodeInput']
export type ComfyViewExposedParam = components['schemas']['ComfyViewExposedParam']
export type ComfyParamTierInfo = components['schemas']['ParamTierInfo']
export type ComfyValidationIssue = components['schemas']['ValidationIssue']
export type ComfyValidateRequest = components['schemas']['ComfyValidateRequest']
export type ComfyValidateResponse = components['schemas']['ComfyValidateResponse']
export type ComfyParamType = ComfyExposedParam['type']
export type ComfySuggestResponse = components['schemas']['ComfySuggestResponse']
export type ComfySuggestItem = components['schemas']['ComfySuggestItem']
export type ComfySuggestDiscarded = components['schemas']['ComfySuggestDiscarded']

export type PriceEstimateResponse = components['schemas']['PriceEstimate']
export type PriceEstimateInputImage = components['schemas']['InputImageEstimateResponse']
export type PriceEstimateUnitPrices = components['schemas']['UnitPricesPer1M']
export type PriceEstimateUnavailableReason = NonNullable<PriceEstimateResponse['unavailable_reason']>

export class ApiError extends Error {
  readonly status: number
  /** `detail` が `{code, message}` の形のときの `code`(埋め込みの API の 409 など。ADR-0033)。 */
  readonly code: string | null
  /** `detail` が `{code, message, field}` の形のときの `field`(どの入力欄が原因か。認証の設定。ADR-0034)。 */
  readonly field: string | null

  constructor(status: number, message: string, code: string | null = null, field: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.field = field
  }
}

/** FastAPI の 422/4xx が返す `detail` を人が読める1つの文字列にする。 */
function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const message = (detail as { message: unknown }).message
    if (typeof message === 'string') return message
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : null))
      .filter((msg): msg is string => msg !== null)
    if (messages.length > 0) return messages.join(' / ')
  }
  return null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetchWithLocale(path, init)
  if (!res.ok) throw await apiErrorFromResponse(path, res)
  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}

function fetchWithLocale(path: string, init?: RequestInit): Promise<Response> {
  // サーバーの文言(エラー、capabilities の見出し)を画面の言語に合わせる(ADR-0015)。
  const headers = new Headers(init?.headers)
  headers.set('Accept-Language', getLocale())
  return fetch(path, { ...init, headers })
}

/** 失敗の応答から ApiError を作る(本文の `detail` を読む)。 */
async function apiErrorFromResponse(path: string, res: Response): Promise<ApiError> {
  // oidc モードでセッションが切れた(ADR-0019)。`/api/auth/*` 自体の 401 は
  // 未ログイン時の正常応答(`/me` は 401 を返さないが、念のため対象から外す)。
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    markUnauthenticated()
  }
  let message = res.statusText || `HTTP ${res.status}`
  let code: string | null = null
  let field: string | null = null
  try {
    const body: unknown = await res.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail
      message = formatDetail(detail) ?? message
      if (detail && typeof detail === 'object' && 'code' in detail) {
        const value = (detail as { code: unknown }).code
        code = typeof value === 'string' ? value : null
      }
      if (detail && typeof detail === 'object' && 'field' in detail) {
        const value = (detail as { field: unknown }).field
        field = typeof value === 'string' ? value : null
      }
    }
  } catch {
    // レスポンスが JSON でない場合はそのまま statusText を使う。
  }
  return new ApiError(res.status, message, code, field)
}

function toQuery(
  params: Record<string, string | number | boolean | readonly string[] | undefined | null>,
): string {
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    // 配列は同じキーを繰り返す(`tag=a&tag=b`)。空の要素は送らない。
    if (Array.isArray(value)) {
      for (const item of value as readonly string[]) if (item !== '') qs.append(key, item)
      continue
    }
    // 真偽値のフラグ(`ungrouped` など)は true のときだけ送る(false は既定と同じなので省く)。
    if (value === undefined || value === null || value === '' || value === false) continue
    qs.set(key, String(value))
  }
  const s = qs.toString()
  return s ? `?${s}` : ''
}

// -- 認証(ADR-0019) ----------------------------------------------------------

/** `AuthGate` が起動時に1回だけ呼ぶ。none モードは常に `user: null`。 */
export function getAuthMe(): Promise<AuthMeResponse> {
  return request('/api/auth/me')
}

/** セッションを失効させ、IdP のログアウト URL(`redirect_url`)を返す。画面がそこへ遷移する。 */
export function logout(): Promise<AuthLogoutResponse> {
  return request('/api/auth/logout', { method: 'POST' })
}

// -- アバター(ADR-0020、oidc モードだけ) ---------------------------------------
// none モードでは3つとも 404(`t("auth.disabled")`)。

/**
 * multipart。PNG/JPEG/WebP、20MB 以下。413(大きすぎ)・422(読めない画像/はみ出したトリミング範囲)は
 * `ApiError.message` に detail がそのまま入る。`crop`(元画像のピクセル座標)を省略すると
 * サーバー側が中央の正方形を使う。
 */
export function uploadAvatar(file: File, crop?: CropRect): Promise<AuthUser> {
  const form = new FormData()
  form.append('file', file)
  if (crop) form.append('crop', JSON.stringify(crop))
  return request('/api/users/me/avatar', { method: 'POST', body: form })
}

/**
 * 既存の Asset(削除済みでないもの)をその時点の原本から複製してアバターにする。
 * 存在しない/削除済み/はみ出した `crop` は 422。`crop` を省略すると中央の正方形。
 */
export function setAvatarFromAsset(assetId: string, crop?: CropRect): Promise<AuthUser> {
  return request('/api/users/me/avatar/from-asset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(crop ? { asset_id: assetId, crop } : { asset_id: assetId }),
  })
}

/** アバターを消す(頭文字の表示に戻る)。 */
export function deleteAvatar(): Promise<AuthUser> {
  return request('/api/users/me/avatar', { method: 'DELETE' })
}

export function getCapabilities(): Promise<CapabilitiesResponse> {
  return request('/api/capabilities')
}

/** 「GAKEI について」(ADR-0021 3章)。バージョンと commit を返す。 */
export function getAbout(): Promise<AboutResponse> {
  return request('/api/about')
}

export function listRuns(
  params: operations['list_runs']['parameters']['query'] = {},
): Promise<RunListResponse> {
  return request(`/api/runs${toQuery(params)}`)
}

export type HistoryAssetItem = components['schemas']['HistoryAssetItem']
export type HistoryAssetListResponse = components['schemas']['HistoryAssetListResponse']
export type HistoryGroupSection = components['schemas']['HistoryGroupSection']

/** 画像単位の履歴(グループ・似た画像の順。ADR-0054)。 */
export function listHistoryAssets(
  params: operations['list_history_assets']['parameters']['query'],
): Promise<HistoryAssetListResponse> {
  return request(`/api/history/assets${toQuery(params)}`)
}

export function getRun(runId: string): Promise<RunDetail> {
  return request(`/api/runs/${runId}`)
}

export function createRun(body: RunCreateRequest): Promise<RunCreateResponse> {
  return request('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function cancelRun(runId: string): Promise<components['schemas']['RunCancelResponse']> {
  return request(`/api/runs/${runId}/cancel`, { method: 'POST' })
}

/**
 * 論理削除。終了状態(succeeded/failed/canceled)の Run だけ削除できる(queued/running は
 * サーバー側が 409 を返す)。出力 Asset も合わせて論理削除される。取り消しは無い。
 */
export function deleteRun(runId: string): Promise<void> {
  return request(`/api/runs/${runId}`, { method: 'DELETE' })
}

export type AssetFavoriteResponse = components['schemas']['AssetFavoriteResponse']

/** 見る人のお気に入りにする・外す(冪等。ADR-0055)。 */
export function setAssetFavorite(assetId: string, favorite: boolean): Promise<AssetFavoriteResponse> {
  return request(`/api/assets/${assetId}/favorite`, { method: favorite ? 'PUT' : 'DELETE' })
}

export function getAsset(assetId: string): Promise<AssetDetail> {
  return request(`/api/assets/${assetId}`)
}

/** 論理削除。ファイルは残るので Run 詳細・系列グラフからは「削除済み」として参照できる。 */
export function deleteAsset(assetId: string): Promise<void> {
  return request(`/api/assets/${assetId}`, { method: 'DELETE' })
}

/**
 * 論理削除の取り消し。未削除、または生んだ Run が削除済みの場合は 404/409(detail に理由)。
 * `AssetDetail.restorable` が true のときだけ呼べる想定。
 */
export function restoreAsset(assetId: string): Promise<AssetDetail> {
  return request(`/api/assets/${assetId}/restore`, { method: 'POST' })
}

/**
 * `params.group_id` を渡すと、そのグループのメンバーだけに絞る(ADR-0022)。`kind` と併用できる。
 * `kind` は配列で、繰り返して送る(指定した種類のどれか。省くと全種類。ADR-0035)。
 */
export function listAssets(
  params: operations['list_assets']['parameters']['query'] = {},
): Promise<components['schemas']['AssetListResponse']> {
  return request(`/api/assets${toQuery(params)}`)
}

/**
 * POST /api/assets(multipart)。Content-Type は付けない(FormData を渡すと
 * fetch がボーダリ付きで自動設定するため、手動で付けると壊れる)。
 *
 * 応答の `ingest_outcome`(ADR-0014)が `matched_existing` のときは、新しい行を作らず
 * 既存の Asset(`id` はその既存 Asset のもの)を返す。呼び出し側はその id をそのまま
 * 入力チップ等に使ってよい。
 *
 * `sourceAssetId` と `replacesAssetId` は同時に指定できない(サーバーが 422 を返す)。
 * `replacesAssetId` は未使用スケッチ・未使用マスクの再編集(ADR-0010、2026-09-25 追記)専用で、
 * `source_asset_id` はサーバー側が決める(呼び出し側は送らない。マスクは常に null のまま)。
 */
export function createAsset(
  file: File | Blob,
  kind: components['schemas']['Body_create_asset']['kind'],
  sourceAssetId?: string,
  replacesAssetId?: string,
): Promise<AssetUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  form.append('kind', kind)
  // 上描きスケッチの下地 Asset(ADR-0010)。kind=sketch のときだけ意味を持つ。
  if (sourceAssetId) form.append('source_asset_id', sourceAssetId)
  // 未使用スケッチの再編集(ADR-0010、2026-09-25 追記)。kind=sketch のときだけ意味を持つ。
  if (replacesAssetId) form.append('replaces_asset_id', replacesAssetId)
  return request('/api/assets', { method: 'POST', body: form })
}

/**
 * 配置の結果(ADR-0050 3章)を `kind=composite` で保存する。`composition` は配置のレシピの JSON。
 * `replacesAssetId` は配置の作り直しで、元の配置を置き換える(未使用なら元を論理削除する。
 * 使用済みなら残す。判定はサーバー)。
 */
export function createCompositeAsset(
  file: Blob,
  composition: unknown,
  replacesAssetId?: string,
): Promise<AssetUploadResponse> {
  const form = new FormData()
  form.append('file', file, 'composite.png')
  form.append('kind', 'composite')
  form.append('composition', JSON.stringify(composition))
  if (replacesAssetId) form.append('replaces_asset_id', replacesAssetId)
  return request('/api/assets', { method: 'POST', body: form })
}

/** up/down は 0〜255 ホップ。省略時はサーバー既定(どちらも 255。量はノード数の上限 1000 で抑える。ADR-0014 6章)。 */
export function getAssetLineage(
  assetId: string,
  params: operations['get_asset_lineage']['parameters']['query'] = {},
): Promise<AssetLineageResponse> {
  return request(`/api/assets/${assetId}/lineage${toQuery(params)}`)
}

// -- グループ(ADR-0022) ------------------------------------------------------
// 階層なしのフラットなグループ。証跡ではないので更新・削除は自由(ADR-0003 の対象外)。

/**
 * 削除済みでないグループを利用者が決めた順(`position` 昇順)で全件。ページングなし(prompt-sets と同じ)。
 * `params.kind` を渡すと、`member_count` と `cover_asset_id` をその種類だけで数える(ADR-0035)。
 */
export function listAssetGroups(
  params: operations['list_asset_groups']['parameters']['query'] = {},
): Promise<AssetGroupListResponse> {
  return request(`/api/asset-groups${toQuery(params)}`)
}

export function createAssetGroup(name: string): Promise<AssetGroupRow> {
  return request('/api/asset-groups', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export function updateAssetGroup(groupId: string, name: string): Promise<AssetGroupRow> {
  return request(`/api/asset-groups/${groupId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

/**
 * 並べ替え。削除済みでない全グループの id を望む順に渡す(過不足・重複があれば 422)。
 * 並べ替え後の一覧を返す。
 */
export function reorderAssetGroups(groupIds: string[]): Promise<AssetGroupListResponse> {
  return request('/api/asset-groups/order', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group_ids: groupIds }),
  })
}

/** 論理削除。メンバー行は残るが、一覧・絞り込みからは消える。 */
export function deleteAssetGroup(groupId: string): Promise<void> {
  return request(`/api/asset-groups/${groupId}`, { method: 'DELETE' })
}

/** そのグループへ移す(別のグループに入っていれば外してから入れる。ADR-0022)。既に入っているものは無視される。1〜200件。 */
export function addAssetsToGroup(groupId: string, assetIds: string[]): Promise<AssetGroupRow> {
  return request(`/api/asset-groups/${groupId}/assets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_ids: assetIds }),
  })
}

// -- グループの提案(ADR-0056) ----------------------------------------------------------

export type GroupSuggestion = components['schemas']['GroupSuggestion']
export type GroupSuggestionsResponse = components['schemas']['GroupSuggestionsResponse']
export type GroupSuggestionScope = GroupSuggestionsResponse['scope']
export type GroupSuggestionGranularity = GroupSuggestionsResponse['granularity']

/** グループの提案(埋め込みが使えなければ 409)。何も保存しない。 */
export function listGroupSuggestions(
  params: NonNullable<operations['list_group_suggestions']['parameters']['query']> = {},
): Promise<GroupSuggestionsResponse> {
  return request(`/api/asset-groups/suggestions${toQuery(params)}`)
}

/**
 * 提案を受け入れる。`name` なら新しいグループを作って入れ、`target_group_id` ならそのグループへ移す
 * (どちらか一方。1回のトランザクション)。
 */
export function acceptGroupSuggestion(
  body: { asset_ids: string[] } & ({ name: string; target_group_id?: never } | { target_group_id: string; name?: never }),
): Promise<AssetGroupRow> {
  return request('/api/asset-groups/suggestions/accept', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** 入っていないものは無視される。 */
export function removeAssetsFromGroup(groupId: string, assetIds: string[]): Promise<AssetGroupRow> {
  return request(`/api/asset-groups/${groupId}/assets/remove`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_ids: assetIds }),
  })
}

// -- プロンプトセット -------------------------------------------------------

export function listPromptSets(): Promise<PromptSetListResponse> {
  return request('/api/prompt-sets')
}

/** GET /api/search。q が空だと 422 になるので、呼び出し側は空文字なら呼ばないこと。 */
export function search(
  params: operations['search']['parameters']['query'],
): Promise<SearchResponse> {
  return request(`/api/search${toQuery(params)}`)
}

export function createPromptSet(body: PromptSetCreateRequest): Promise<PromptSetResponse> {
  return request('/api/prompt-sets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function updatePromptSet(
  promptSetId: string,
  body: PromptSetUpdateRequest,
): Promise<PromptSetResponse> {
  return request(`/api/prompt-sets/${promptSetId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deletePromptSet(promptSetId: string): Promise<void> {
  return request(`/api/prompt-sets/${promptSetId}`, { method: 'DELETE' })
}

export function addPromptSetItem(
  promptSetId: string,
  body: PromptSetItemAppendRequest,
): Promise<PromptSetItemResponse> {
  return request(`/api/prompt-sets/${promptSetId}/items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/**
 * body に含めたキーだけが更新される(`label` を明示的に `null` にするとクリアできる)。
 * このため `undefined` のキーは JSON.stringify で自然に落ちる(=変更しない)。
 */
export function updatePromptSetItem(
  promptSetId: string,
  itemId: string,
  body: PromptSetItemUpdateRequest,
): Promise<PromptSetItemResponse> {
  return request(`/api/prompt-sets/${promptSetId}/items/${itemId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deletePromptSetItem(promptSetId: string, itemId: string): Promise<void> {
  return request(`/api/prompt-sets/${promptSetId}/items/${itemId}`, { method: 'DELETE' })
}

// -- パラメーターセット(ADR-0040) -----------------------------------------

export function listParameterSets(): Promise<ParameterSetListResponse> {
  return request('/api/parameter-sets')
}

export function createParameterSet(body: ParameterSetCreateRequest): Promise<ParameterSetResponse> {
  return request('/api/parameter-sets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/**
 * body に含めたキーだけが更新される(`model`・`prompt` を明示的に `null` にすると「保存しない」に戻る)。
 */
export function updateParameterSet(
  parameterSetId: string,
  body: ParameterSetUpdateRequest,
): Promise<ParameterSetResponse> {
  return request(`/api/parameter-sets/${parameterSetId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deleteParameterSet(parameterSetId: string): Promise<void> {
  return request(`/api/parameter-sets/${parameterSetId}`, { method: 'DELETE' })
}

// -- プロンプトテンプレート(実験) ------------------------------------------

export function listPromptTemplates(): Promise<PromptTemplateListResponse> {
  return request('/api/prompt-templates')
}

export function getPromptTemplate(templateId: string): Promise<PromptTemplateResponse> {
  return request(`/api/prompt-templates/${templateId}`)
}

export function createPromptTemplate(body: PromptTemplateWriteRequest): Promise<PromptTemplateResponse> {
  return request('/api/prompt-templates', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/**
 * 丸ごと置換。id を渡した子(スロット・候補・組み合わせ)は残して更新し、本文に無い子は
 * 論理削除される。残したい子は必ず id を付けて本文に入れる。
 */
export function replacePromptTemplate(
  templateId: string,
  body: PromptTemplateWriteRequest,
): Promise<PromptTemplateResponse> {
  return request(`/api/prompt-templates/${templateId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deletePromptTemplate(templateId: string): Promise<void> {
  return request(`/api/prompt-templates/${templateId}`, { method: 'DELETE' })
}

/** 選択を渡して組み立て結果を返す(生成はしない。確認用)。 */
export function buildPromptTemplate(
  templateId: string,
  body: PromptBuildRequest,
): Promise<PromptBuildResponse> {
  return request(`/api/prompt-templates/${templateId}/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// -- 参考価格 ----------------------------------------------------------------

/** `input_asset_ids` は API ではカンマ区切りの文字列だが、呼び出し側は配列で渡す。 */
export type PriceEstimateParams = Omit<
  operations['estimate_price']['parameters']['query'],
  'input_asset_ids'
> & {
  /** role=image の入力 Asset id(position 順)。空配列/未指定なら送らない。 */
  input_asset_ids?: string[]
}

export function estimatePrice(params: PriceEstimateParams): Promise<PriceEstimateResponse> {
  const { input_asset_ids, ...rest } = params
  const qs = new URLSearchParams(toQuery(rest).replace(/^\?/, ''))
  if (input_asset_ids && input_asset_ids.length > 0) {
    qs.set('input_asset_ids', input_asset_ids.join(','))
  }
  const s = qs.toString()
  return request(`/api/pricing/estimate${s ? `?${s}` : ''}`)
}

// -- 設定(OpenAI API キー、ADR-0012) -----------------------------------------

export function getOpenAiKeyStatus(): Promise<OpenAIKeyStatus> {
  return request('/api/settings/openai-key')
}

/** 有効性を OpenAI に確認してから保存するため、返るまで最大15秒程度かかる。 */
export function setOpenAiKey(apiKey: string): Promise<OpenAIKeyStatus> {
  return request('/api/settings/openai-key', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  })
}

export function deleteOpenAiKey(): Promise<OpenAIKeyStatus> {
  return request('/api/settings/openai-key', { method: 'DELETE' })
}

// -- 設定(OpenAI の接続先(Base URL)、ADR-0017) --------------------------------
// キーと同じ扱い: 環境変数が優先され、画面からは変更・削除できない。値は秘密ではないので
// 全文を返す。

export function getOpenAiBaseUrlStatus(): Promise<OpenAIBaseUrlStatus> {
  return request('/api/settings/openai-base-url')
}

/** 有効なキーがあれば OpenAI 互換 API への確認を行うため、最大15秒程度かかることがある。 */
/**
 * 接続先(Base URL)を確かめてから保存する。`apiKey` を渡すと、そのキーで確かめて両方を保存する。画面で保存した
 * キーがあり、オリジンが変わるときは必須(省くと 422)。
 */
export function setOpenAiBaseUrl(baseUrl: string, apiKey?: string): Promise<OpenAIBaseUrlStatus> {
  return request('/api/settings/openai-base-url', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(apiKey ? { base_url: baseUrl, api_key: apiKey } : { base_url: baseUrl }),
  })
}

export function deleteOpenAiBaseUrl(): Promise<OpenAIBaseUrlStatus> {
  return request('/api/settings/openai-base-url', { method: 'DELETE' })
}

// -- 全般設定(moderation、ComfyUI タイムアウト。ADR-0009、ADR-0013 7章) -------------

export function getGeneralSettings(): Promise<GeneralSettingsResponse> {
  return request('/api/settings/general')
}

/**
 * 省略したキーは変更しない。値を明示的に `null` にすると、保存済みの値を消して
 * 環境変数・組み込みの既定値に戻す(`PromptSetItemUpdateRequest` と同じ考え方)。
 */
export function updateGeneralSettings(
  body: GeneralSettingsUpdateRequest,
): Promise<GeneralSettingsResponse> {
  return request('/api/settings/general', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// -- ComfyUI ワークフロー(ADR-0013) ------------------------------------------

export function getComfyUIStatus(): Promise<ComfyUIStatus> {
  return request('/api/comfyui/status')
}

/** 設定は変えない。省略時(`url` 未指定)はサーバーが現在の有効な URL で試す。 */
export function testComfyUIConnection(url?: string): Promise<ComfyUIConnectionTestResponse> {
  return request('/api/comfyui/connection/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: url ?? null }),
  })
}

/**
 * 接続・URL の変更。ループバック以外の URL は `allowNonLoopback` を true にしないと 422 になる
 * (ADR-0013 7章。画面での確認チェックに対応)。ComfyUI の Run が queued/running の間は 409。
 */
export function setComfyUIConnection(
  url: string,
  allowNonLoopback = false,
): Promise<ComfyUIStatus> {
  return request('/api/comfyui/connection', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, allow_non_loopback: allowNonLoopback }),
  })
}

/** 切り離す。登録済みワークフローと過去の Run は消えない。ComfyUI の Run が queued/running の間は 409。 */
export function detachComfyUIConnection(): Promise<ComfyUIStatus> {
  return request('/api/comfyui/connection', { method: 'DELETE' })
}

/** 無効にする(ADR-0013 7章)。URL は残したまま、モデルの選択肢から外す。 */
export function disableComfyUIConnection(): Promise<ComfyUIStatus> {
  return request('/api/comfyui/connection/disable', { method: 'POST' })
}

/** 有効に戻す。保存してある同じ URL で接続する。 */
export function enableComfyUIConnection(): Promise<ComfyUIStatus> {
  return request('/api/comfyui/connection/enable', { method: 'POST' })
}

/** 保存はしない(登録画面向けの提案)。UI 形式の JSON などは 422(detail をそのまま表示する)。 */
export function analyzeComfyWorkflow(
  template: Record<string, unknown>,
): Promise<ComfyAnalyzeResponse> {
  return request('/api/comfyui/workflows/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ template }),
  })
}

/**
 * 本文の `template` に、読み込んだファイルの JSON の文字列をそのまま入れる。ブラウザで JSON を
 * 読み直すと `1.0` が `1` になり、サーバーが値から型(FLOAT)を推定できなくなるため(ADR-0052)。
 * `text` は `JSON.parse` できることを呼び出し側で確かめておく。
 */
export function jsonBodyWithRawTemplate(rest: Record<string, unknown>, text: string): string {
  const inner = JSON.stringify(rest).slice(1, -1)
  return `{"template":${text}${inner ? `,${inner}` : ''}}`
}

/** ファイルの JSON の文字列のまま読み取る(数の型を保つ)。 */
export function analyzeComfyWorkflowText(text: string): Promise<ComfyAnalyzeResponse> {
  return request('/api/comfyui/workflows/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: jsonBodyWithRawTemplate({}, text),
  })
}

/** 登録済みのワークフローの、保存したテンプレートを読み取る(編集画面。数の型を保つ)。 */
export function analyzeSavedComfyWorkflow(workflowId: string): Promise<ComfyAnalyzeResponse> {
  return request('/api/comfyui/workflows/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workflow_id: workflowId }),
  })
}

/**
 * アシスタント(LLM)で差し込み先を推定する(ADR-0052 6章。保存しない)。`workflowId` があれば
 * 保存したテンプレートを、無ければ `templateText`(読み込んだファイルの JSON の文字列のまま)を使う。
 */
export function suggestComfyWorkflowBindings(input: {
  workflowId: string | null
  templateText: string | null
  name: string
}): Promise<ComfySuggestResponse> {
  const name = input.name.trim().slice(0, 100) || null
  const body =
    input.workflowId !== null
      ? JSON.stringify({ workflow_id: input.workflowId, name })
      : jsonBodyWithRawTemplate({ name }, input.templateText ?? '{}')
  return request('/api/comfyui/workflows/suggest-bindings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  })
}

/** 保存せずに、保存時と同じ検証だけを掛ける(ADR-0052 3章)。画面が値を変えるたびに呼ぶ。 */
export function validateComfyWorkflow(body: ComfyValidateRequest): Promise<ComfyValidateResponse> {
  return request('/api/comfyui/workflows/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function listComfyWorkflows(): Promise<ComfyWorkflowListResponse> {
  return request('/api/comfyui/workflows')
}

export function getComfyWorkflow(workflowId: string): Promise<ComfyWorkflowDetail> {
  return request(`/api/comfyui/workflows/${workflowId}`)
}

/** 生成画面からの閲覧(ADR-0052 7章)。図に要る構造と値だけで、秘密に見える値は伏せてある。 */
export function viewComfyWorkflow(workflowId: string): Promise<ComfyWorkflowView> {
  return request(`/api/comfyui/workflows/${encodeURIComponent(workflowId)}/view`)
}

export function createComfyWorkflow(
  body: ComfyWorkflowCreateRequest,
  /** 読み込んだファイルの JSON の文字列(数の型を保って送る)。省くと `body.template` を送る。 */
  templateText?: string | null,
): Promise<ComfyWorkflowDetail> {
  return request('/api/comfyui/workflows', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: comfyWorkflowBody(body, templateText),
  })
}

/**
 * 更新。`template` を省くと保存済みのテンプレートのまま(数の型を保つ)。ファイルを差し替えたときは
 * `templateText` にファイルの JSON の文字列を渡す。
 */
export function updateComfyWorkflow(
  workflowId: string,
  body: ComfyWorkflowUpdateRequest,
  templateText?: string | null,
): Promise<ComfyWorkflowDetail> {
  return request(`/api/comfyui/workflows/${workflowId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: comfyWorkflowBody(body, templateText),
  })
}

function comfyWorkflowBody(
  body: ComfyWorkflowCreateRequest | ComfyWorkflowUpdateRequest,
  templateText: string | null | undefined,
): string {
  if (!templateText || body.template === undefined || body.template === null) return JSON.stringify(body)
  const { template: _template, ...rest } = body
  return jsonBodyWithRawTemplate(rest, templateText)
}

/** 論理削除。過去の Run の記録(run.params.comfyui_prompt に確定済みのグラフを保存済み)は変わらない。 */
export function deleteComfyWorkflow(workflowId: string): Promise<void> {
  return request(`/api/comfyui/workflows/${workflowId}`, { method: 'DELETE' })
}

// -- SD WebUI(A1111 互換の API。ADR-0038) ------------------------------------

export type SdWebuiStatus = components['schemas']['SdWebuiStatusResponse']
export type SdWebuiConnectionTestResponse = components['schemas']['SdWebuiConnectionTestResponse']
export type SdWebuiTimeoutSetting = components['schemas']['SdWebuiTimeoutSetting']

export function getSdWebuiStatus(): Promise<SdWebuiStatus> {
  return request('/api/sdwebui/status')
}

/**
 * 設定は変えない。`url` を省くとサーバーが今の有効な URL で試す。資格情報は両方そろえて渡すと
 * その値で試し(保存しない)、省くと保存済みの資格情報を使う。
 */
export function testSdWebuiConnection(params: {
  url?: string
  credentials?: { username: string; password: string }
}): Promise<SdWebuiConnectionTestResponse> {
  return request('/api/sdwebui/connection/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      url: params.url ?? null,
      ...(params.credentials ?? {}),
    }),
  })
}

/**
 * 接続・URL の変更。ループバック以外は `allowNonLoopback` が要る(422)。SD WebUI の Run が待機中・実行中なら 409。
 * `credentials` を渡すと、接続先と一緒に保存する(この URL のオリジンに結び付ける)。保存済みの資格情報があり、
 * オリジンが変わるときは必須(省くと 422)。
 */
export function setSdWebuiConnection(
  url: string,
  allowNonLoopback = false,
  credentials?: { username: string; password: string },
): Promise<SdWebuiStatus> {
  return request('/api/sdwebui/connection', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, allow_non_loopback: allowNonLoopback, ...(credentials ?? {}) }),
  })
}

/** 切り離す。過去の Run と資格情報は消えない。 */
export function detachSdWebuiConnection(): Promise<SdWebuiStatus> {
  return request('/api/sdwebui/connection', { method: 'DELETE' })
}

/** 無効にする(ADR-0038 6章)。URL・資格情報・待つ上限は残したまま、モデルの選択肢から外す。 */
export function disableSdWebuiConnection(): Promise<SdWebuiStatus> {
  return request('/api/sdwebui/connection/disable', { method: 'POST' })
}

/** 有効に戻す。保存してある同じ設定で接続する。 */
export function enableSdWebuiConnection(): Promise<SdWebuiStatus> {
  return request('/api/sdwebui/connection/enable', { method: 'POST' })
}

/** Basic 認証の資格情報を保存する。応答に値は含まれない(`credentials_set` だけ)。 */
export function setSdWebuiCredentials(username: string, password: string): Promise<SdWebuiStatus> {
  return request('/api/sdwebui/credentials', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export function deleteSdWebuiCredentials(): Promise<SdWebuiStatus> {
  return request('/api/sdwebui/credentials', { method: 'DELETE' })
}

/** WebUI にチェックポイントと LoRA の一覧を読み直させ、GAKEI の一覧のキャッシュも捨てる(管理者だけ)。 */
export function refreshSdWebui(): Promise<SdWebuiStatus> {
  return request('/api/sdwebui/refresh', { method: 'POST' })
}

export type SdWebuiLora = components['schemas']['SdWebuiLora']

/**
 * 接続先の LoRA の一覧(ADR-0038 8章)。name・alias・ベースモデル・トリガーの候補だけ。
 * 接続していない・接続先から取れないときは 409。
 */
export function listSdWebuiLoras(): Promise<{ items: SdWebuiLora[] }> {
  return request('/api/sdwebui/loras')
}

export type SdWebuiImportParamsResponse = components['schemas']['SdWebuiImportParamsResponse']

/**
 * 画像の生成情報(A1111 形式)から SD WebUI の生成フォームの値を作る(ADR-0038 9章)。
 * 手元のファイル(サーバーは保存しない)か、ストックの画像(asset_id)のどちらか。
 * 未接続は 409、生成情報が無い・A1111 形式でなければ 422、見えない Asset は 404、大きすぎれば 413。
 */
export function importSdWebuiParams(
  input: { file: Blob } | { assetId: string },
): Promise<SdWebuiImportParamsResponse> {
  if ('file' in input) {
    const form = new FormData()
    form.append('file', input.file, input.file instanceof File ? input.file.name : 'image')
    return request('/api/sdwebui/import-params', { method: 'POST', body: form })
  }
  return request('/api/sdwebui/import-params', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_id: input.assetId }),
  })
}

// -- タイトルとタグ(ADR-0024) --------------------------------------------------
// 編集はログイン者全員。マスクと削除済みの Asset は 409(detail に理由)。いずれも更新後の注釈を返す。

/** null・空文字はタイトルを消す(以後も自動では付けない)。200 字を超えると 422。 */
export function updateAssetTitle(assetId: string, title: string | null): Promise<AssetAnnotationResponse> {
  return request(`/api/assets/${assetId}/title`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

/** 名前はサーバーで正規化される(NFKC、小文字化など)。付けたタグは source=user。 */
export function addAssetTag(assetId: string, name: string): Promise<AssetAnnotationResponse> {
  return request(`/api/assets/${assetId}/tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

/** 自動のタグを消すと、再推定でも付け直さない(サーバー側で記録する)。 */
export function removeAssetTag(assetId: string, name: string): Promise<AssetAnnotationResponse> {
  return request(`/api/assets/${assetId}/tags/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

/** 再推定を待ち行列に入れる。使えるエンジンが無ければ 409。人が決めたタイトルとタグは保たれる。 */
export function annotateAsset(assetId: string): Promise<AssetAnnotationResponse> {
  return request(`/api/assets/${assetId}/annotate`, { method: 'POST' })
}

/** 件数の多い順。`q` は部分一致(正規化してから比べる)。 */
export function listTags(params: operations['list_tags']['parameters']['query'] = {}): Promise<TagListResponse> {
  return request(`/api/tags${toQuery(params)}`)
}

/**
 * プロンプトのタグ編集の候補(ADR-0039 2章)。WD Tagger の語彙と GAKEI のタグ(英数字のもの)、
 * 前方一致が先。名前はエスケープ前(`_` は空白)。
 */
export function suggestPromptTags(
  params: operations['suggest_prompt_tags']['parameters']['query'] = {},
): Promise<TagSuggestionResponse> {
  return request(`/api/tags/suggestions${toQuery(params)}`)
}

/**
 * タグ名 → 辞書の代表の訳(ADR-0041 4章)。訳の無いタグは含めない。キーは渡した名前のまま。
 * 1回に送れるのは 200 件まで。
 */
export function getTagTranslations(names: readonly string[]): Promise<{ translations?: Record<string, string> }> {
  return request('/api/tags/translations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ names }),
  })
}

// -- タグ辞書(ADR-0041 1章。管理者だけ) -------------------------------------------

export function listTagDictionaries(): Promise<TagDictionaryListResponse> {
  return request('/api/settings/tag-dictionaries')
}

/**
 * CSV か、CSV を含む zip を登録する(202。取り込みはサーバーの中で続き、完了は一覧の取得で確かめる)。
 * 100MB を超えると 413、種類を判定できない・zip が不正などは 422(`detail` は翻訳済み)。
 */
export function uploadTagDictionary(file: File, categoryScheme?: TagCategoryScheme): Promise<TagDictionaryListResponse> {
  const form = new FormData()
  form.append('file', file, file.name)
  if (categoryScheme) form.append('category_scheme', categoryScheme)
  return request('/api/settings/tag-dictionaries', { method: 'POST', body: form })
}

export function updateTagDictionary(id: string, body: TagDictionaryUpdateRequest): Promise<TagDictionaryItem> {
  return request(`/api/settings/tag-dictionaries/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** 取り込み中は 409。 */
export function deleteTagDictionary(id: string): Promise<void> {
  return request(`/api/settings/tag-dictionaries/${id}`, { method: 'DELETE' })
}

/** 画像のタグをプロンプトにするときの並び(ADR-0039 1章)。名前はエスケープ前。 */
export function getAssetPromptTags(assetId: string): Promise<PromptTagsResponse> {
  return request(`/api/assets/${assetId}/prompt-tags`)
}

/** 全ログイン者が読める(ビューアの「再推定」を出すかどうかに `usable_engines` を使う)。 */
export function getAnnotationSettings(): Promise<AnnotationSettingsResponse> {
  return request('/api/settings/annotation')
}

/**
 * 管理者のみ。省略した項目は変更しない。`profiles` は書いたマスだけ変わる(ComfyUI の画像の
 * マスに null を送ると「既定と同じ」に戻す。ADR-0024 8章)。
 */
export function updateAnnotationSettings(body: AnnotationSettingsUpdateRequest): Promise<AnnotationSettingsResponse> {
  return request('/api/settings/annotation', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** ONNX タガーのモデルのダウンロードを始める(202)。進捗は `getAnnotationSettings` をポーリングして見る。 */
export function downloadOnnxModel(model: OnnxModelName): Promise<AnnotationSettingsResponse> {
  return request('/api/settings/annotation/onnx/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
}

export function deleteOnnxModel(model: OnnxModelName): Promise<AnnotationSettingsResponse> {
  return request(`/api/settings/annotation/onnx/${encodeURIComponent(model)}`, { method: 'DELETE' })
}

/** 推定を一度も実行していない画像(削除済みを除く)をまとめて待ち行列に入れる。 */
export function backfillAnnotations(): Promise<AnnotationBackfillResponse> {
  return request('/api/settings/annotation/backfill', { method: 'POST' })
}

/**
 * レーティングの判定を待ち行列に入れる(管理者。ADR-0049 3章)。未判定と、古い版の規則で自動判定した画像が
 * 対象。判定が無効、またはモデルが無ければ 409。
 */
export function backfillContentRatings(): Promise<AnnotationBackfillResponse> {
  return request('/api/settings/annotation/content-rating/backfill', { method: 'POST' })
}

// -- レーティング(ADR-0049) ---------------------------------------------------
// マスクと削除済みの Asset は 409(detail に理由)。いずれも更新後の状態を返す。

/** 人が段階を選ぶ(以後の再判定で変わらない)。`null` で自動の段階に戻す。 */
export function updateAssetContentRating(
  assetId: string,
  level: ContentRatingLevel | null,
): Promise<AssetContentRatingResponse> {
  return request(`/api/assets/${assetId}/content-rating`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ level }),
  })
}

/** 再判定を待ち行列に入れる。判定が無効、またはモデルが無ければ 409。 */
export function judgeAssetContentRating(assetId: string): Promise<AssetContentRatingResponse> {
  return request(`/api/assets/${assetId}/content-rating/judge`, { method: 'POST' })
}

// -- ユーザーごとの設定(ADR-0049 6章) -----------------------------------------

/** すべてのキーを返す(保存していないキーは既定値)。個人モードでも使える。 */
export function getUserPreferences(): Promise<UserPreferencesResponse> {
  return request('/api/users/me/preferences')
}

/** 書いたキーだけ変える。知らないキーと形の合わない値は 422。 */
export function updateUserPreferences(body: UserPreferencesUpdateRequest): Promise<UserPreferencesResponse> {
  return request('/api/users/me/preferences', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// -- LLM の接続先(ADR-0032) ----------------------------------------------------

/** 接続先の一覧(全ログイン者)。先頭は組み込みの「OpenAI の設定」。キーは設定済みかどうかだけ。 */
export function listLlmConnections(): Promise<LlmConnectionsResponse> {
  return request('/api/settings/llm-connections')
}

/** 接続先を足す(管理者のみ)。キーは任意。 */
export function createLlmConnection(body: LlmConnectionCreateRequest): Promise<LlmConnectionsResponse> {
  return request('/api/settings/llm-connections', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** 接続先を変える。組み込みの `openai` は `api_style` だけ変えられる(ほかは 409)。 */
export function updateLlmConnection(
  connectionId: string,
  body: LlmConnectionUpdateRequest,
): Promise<LlmConnectionsResponse> {
  return request(`/api/settings/llm-connections/${encodeURIComponent(connectionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** 接続先を消す。組み込みと、どこかの機能で使っている接続先は 409。 */
export function deleteLlmConnection(connectionId: string): Promise<LlmConnectionsResponse> {
  return request(`/api/settings/llm-connections/${encodeURIComponent(connectionId)}`, { method: 'DELETE' })
}

/** 接続先のキー(管理者のみ)。値は応答に載らない(設定済みかどうかだけ)。 */
export function setLlmConnectionApiKey(connectionId: string, apiKey: string): Promise<LlmConnectionsResponse> {
  return request(`/api/settings/llm-connections/${encodeURIComponent(connectionId)}/api-key`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  })
}

export function deleteLlmConnectionApiKey(connectionId: string): Promise<LlmConnectionsResponse> {
  return request(`/api/settings/llm-connections/${encodeURIComponent(connectionId)}/api-key`, {
    method: 'DELETE',
  })
}

// -- 画像の埋め込み(ADR-0033) --------------------------------------------------
// 検索系(文章での検索、似た画像、重複の候補)は、埋め込みが無効・使えないと 409
// (`ApiError.code === 'embeddings_unavailable'`)。似た画像は起点のベクトルの状態でも 409
// (`embedding_pending` / `embedding_failed` / `embedding_missing` / `embedding_not_supported`)。

export type EmbeddingCapabilities = components['schemas']['EmbeddingCapabilities']
export type EmbeddingSettingsResponse = components['schemas']['EmbeddingSettingsResponse']
export type EmbeddingSettingsUpdateRequest = components['schemas']['EmbeddingSettingsUpdateRequest']
export type EmbeddingOnnxModelStatus = components['schemas']['EmbeddingOnnxModelStatus']
export type EmbeddingOnnxModelName = EmbeddingOnnxModelStatus['name']
export type EmbeddingStoredCount = components['schemas']['EmbeddingStoredCount']
export type EmbeddingBackfillResponse = components['schemas']['EmbeddingBackfillResponse']
export type EmbeddingLanguage = EmbeddingOnnxModelStatus['languages'][number]
export type EmbeddingErrorCode = components['schemas']['EmbeddingErrorDetail']['code']
export type AssetEmbeddingStatus = components['schemas']['AssetEmbeddingStatus']
export type SemanticSearchResponse = components['schemas']['SemanticSearchResponse']
export type SemanticAssetHit = components['schemas']['SemanticAssetHit']
export type SemanticRunHit = components['schemas']['SemanticRunHit']
export type SimilarAssetsResponse = components['schemas']['SimilarAssetsResponse']
export type SimilarImageSearchResponse = components['schemas']['SimilarImageSearchResponse']
export type DuplicatesResponse = components['schemas']['DuplicatesResponse']
export type EmbeddingGraphResponse = components['schemas']['EmbeddingGraphResponse']
export type EmbeddingGraphNode = components['schemas']['EmbeddingGraphNode']
export type EmbeddingGraphScoresResponse =
  components['schemas']['EmbeddingGraphScoresResponse']
export type DuplicateGroup = components['schemas']['DuplicateGroup']
export type DuplicateAsset = components['schemas']['DuplicateAsset']

/** 全ログイン者が読める(重複の候補のしきい値の既定などに使う)。 */
export function getEmbeddingSettings(): Promise<EmbeddingSettingsResponse> {
  return request('/api/settings/embeddings')
}

/** 管理者のみ。省略した項目は変更しない。`remote_connection_id` と `remote_model` は null で未設定に戻す。 */
export function updateEmbeddingSettings(body: EmbeddingSettingsUpdateRequest): Promise<EmbeddingSettingsResponse> {
  return request('/api/settings/embeddings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** モデルのダウンロードを始める(202)。進捗は `getEmbeddingSettings` をポーリングして見る。 */
export function downloadEmbeddingModel(model: EmbeddingOnnxModelName): Promise<EmbeddingSettingsResponse> {
  return request('/api/settings/embeddings/onnx/download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
}

/** モデルのファイルを消す(保存済みのベクトルは消さない)。 */
export function deleteEmbeddingModel(model: EmbeddingOnnxModelName): Promise<EmbeddingSettingsResponse> {
  return request(`/api/settings/embeddings/onnx/${encodeURIComponent(model)}`, { method: 'DELETE' })
}

/** 使うモデルのベクトルが無い画像をまとめて待ち行列に入れる。 */
export function backfillEmbeddings(): Promise<EmbeddingBackfillResponse> {
  return request('/api/settings/embeddings/backfill', { method: 'POST' })
}

/** そのモデル(`model_key`)のベクトルをすべて消す。 */
export function deleteEmbeddingVectors(modelKey: string): Promise<EmbeddingSettingsResponse> {
  return request(`/api/settings/embeddings/vectors/${encodeURIComponent(modelKey)}`, { method: 'DELETE' })
}

/** 1枚の埋め込みを、使うモデルで計算し直す(待ち行列に入れる)。 */
export function requestAssetEmbedding(assetId: string): Promise<AssetEmbeddingStatus> {
  return request(`/api/assets/${assetId}/embedding`, { method: 'POST' })
}

/**
 * 文章での検索(類似度の高い順)。`target: 'prompt'` で指示(プロンプト)を探し、Run を `runs` に返す
 * (ADR-0045 5章)。既定の画像は `assets` に返す。
 */
export function semanticSearch(
  params: operations['semantic_search']['parameters']['query'],
): Promise<SemanticSearchResponse> {
  return request(`/api/search/semantic${toQuery(params)}`)
}

/**
 * 手元の画像に似た画像(`POST /api/search/similar-image`、multipart)。画像は Asset にせず、
 * サーバーは画像もベクトルも保存しない。大きすぎれば 413、画像として読めなければ 422、
 * 推論できなければ 503。
 */
export function searchByImage(
  file: Blob,
  params: NonNullable<operations['search_by_image']['parameters']['query']> = {},
  signal?: AbortSignal,
): Promise<SimilarImageSearchResponse> {
  const form = new FormData()
  form.append('file', file, file instanceof File ? file.name : 'image')
  return request(`/api/search/similar-image${toQuery(params)}`, { method: 'POST', body: form, signal })
}

/** 似た画像(起点の画像自身は含めない)。 */
export function similarAssets(assetId: string, limit?: number): Promise<SimilarAssetsResponse> {
  return request(`/api/assets/${assetId}/similar${toQuery({ limit })}`)
}

/** 重複の候補。`threshold` を省くと管理者設定の値。 */
export function embeddingDuplicates(
  params: NonNullable<operations['embedding_duplicates']['parameters']['query']> = {},
): Promise<DuplicatesResponse> {
  return request(`/api/embeddings/duplicates${toQuery(params)}`)
}

/**
 * マップの元データ(ノードと k 近傍。ADR-0033 7章)。各行の先頭は自分自身。
 * `include_lineage` を付けると系列の主たる親の辺(`[親, 子]` の位置)も返す。
 */
export function embeddingGraph(
  params: NonNullable<operations['embedding_graph']['parameters']['query']> = {},
): Promise<EmbeddingGraphResponse> {
  return request(`/api/embeddings/graph${toQuery(params)}`)
}

/**
 * マップのノード全件と検索の文章の類似度。`ids` は同じ絞り込み・同じ `limit` の
 * `embeddingGraph` の `nodes` と同じ並びで、`scores[i]` が `ids[i]` の類似度。
 */
export function embeddingGraphScores(
  params: NonNullable<operations['embedding_graph_scores']['parameters']['query']>,
): Promise<EmbeddingGraphScoresResponse> {
  return request(`/api/embeddings/graph/scores${toQuery(params)}`)
}

// -- 生成のアシスタント(ADR-0048) ----------------------------------------------

export type AssistantConversationResponse = components['schemas']['AssistantConversationResponse']
export type AssistantConversationListResponse = components['schemas']['AssistantConversationListResponse']
export type AssistantConversationDetailResponse = components['schemas']['AssistantConversationDetailResponse']
export type AssistantMessageResponse = components['schemas']['AssistantMessageResponse']
export type AssistantToolCallView = components['schemas']['AssistantToolCallView']
export type AssistantMessageCreateRequest = components['schemas']['AssistantMessageCreateRequest']
export type AssistantFormState = components['schemas']['AssistantFormState']
export type AssistantUndoResponse = components['schemas']['AssistantUndoResponse']
/** 発言の送信の応答(SSE)の1イベント。`type` で見分ける。 */
export type AssistantStreamEvent = components['schemas']['AssistantStreamEvent']['event']
export type AssistantSettingsResponse = components['schemas']['AssistantSettingsResponse']
export type AssistantSettingsUpdateRequest = components['schemas']['AssistantSettingsUpdateRequest']

/** 本人の会話(新しい順)。 */
export function listAssistantConversations(): Promise<AssistantConversationListResponse> {
  return request('/api/assistant/conversations')
}

export function createAssistantConversation(title = ''): Promise<AssistantConversationResponse> {
  return request('/api/assistant/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

export function getAssistantConversation(conversationId: string): Promise<AssistantConversationDetailResponse> {
  return request(`/api/assistant/conversations/${conversationId}`)
}

export function renameAssistantConversation(
  conversationId: string,
  title: string,
): Promise<AssistantConversationResponse> {
  return request(`/api/assistant/conversations/${conversationId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
}

export function deleteAssistantConversation(conversationId: string): Promise<void> {
  return request(`/api/assistant/conversations/${conversationId}`, { method: 'DELETE' })
}

/**
 * 発言を送る。応答は `text/event-stream` なので、本文を読まずに Response を返す(読むのは
 * `features/assistant/assistantStream.ts`)。送る前に断られたとき(404/409/422/429)は ApiError。
 * `signal` で中断すると、サーバーは切断として扱い、処理を止める(ADR-0048 9章)。
 */
export async function openAssistantMessageStream(
  conversationId: string,
  body: AssistantMessageCreateRequest,
  signal?: AbortSignal,
): Promise<Response> {
  const path = `/api/assistant/conversations/${conversationId}/messages`
  const res = await fetchWithLocale(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) throw await apiErrorFromResponse(path, res)
  return res
}

/** プロンプトセットへの書き込みを元に戻す。その後に対象が変わっていれば 409。 */
export function undoAssistantWrite(messageId: string): Promise<AssistantUndoResponse> {
  return request(`/api/assistant/undo/${messageId}`, { method: 'POST' })
}

/** 全ログイン者が読める(パネルを出すかの判断。`usable`)。 */
export function getAssistantSettings(): Promise<AssistantSettingsResponse> {
  return request('/api/settings/assistant')
}

/** 管理者のみ。書いた項目だけ変える。不正な値は 422。 */
export function updateAssistantSettings(body: AssistantSettingsUpdateRequest): Promise<AssistantSettingsResponse> {
  return request('/api/settings/assistant', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// -- 領域の四角の検出(ADR-0053 7章) ----------------------------------------------

/** 全ログイン者が読める(検出のボタンを出すかの判断。`usable`)。 */
export function getRegionDetectionSettings(): Promise<RegionDetectionSettingsResponse> {
  return request('/api/settings/region-detection')
}

/** 管理者のみ。書いた項目だけ変える。不正な値は 422。 */
export function updateRegionDetectionSettings(
  body: RegionDetectionSettingsUpdateRequest,
): Promise<RegionDetectionSettingsResponse> {
  return request('/api/settings/region-detection', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/**
 * 見える Asset の画像で四角の候補を検出する。結果は Asset の検出の履歴に保存し、応答の `detection` にも入る。10 秒を超えることがあるので、
 * 取り消せるよう `signal` を受ける。断られたときの `ApiError.message` はサーバーの文言。
 */
export function detectAssetRegions(
  assetId: string,
  query: string | null,
  signal?: AbortSignal,
): Promise<RegionDetectResponse> {
  return request(`/api/assets/${assetId}/regions/detect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: query && query.trim() ? query.trim() : null }),
    signal,
  })
}

/** Asset の検出の履歴(新しい順。最大 20 件)。検出が無効でも読める。 */
export function listAssetRegionDetections(assetId: string): Promise<RegionDetectionListResponse> {
  return request(`/api/assets/${assetId}/regions/detections`)
}

/** 検出の履歴から1件消す(検出した本人か管理者)。 */
export function deleteAssetRegionDetection(assetId: string, detectionId: string): Promise<void> {
  return request(`/api/assets/${assetId}/regions/detections/${detectionId}`, { method: 'DELETE' })
}

// -- 接続先(`locality`。ADR-0051 3章。管理者のみ) ---------------------------------

export type ProviderLocalitiesResponse = components['schemas']['ProviderLocalitiesResponse']
export type ProviderLocalitySetting = components['schemas']['ProviderLocalitySetting']
export type ProviderLocalitiesUpdateRequest = components['schemas']['ProviderLocalitiesUpdateRequest']
/** 接続の接続先(外部 / ローカル)。 */
export type Locality = ProviderLocalitySetting['value']
/** ワークフローの接続先。`unset` は「接続の設定に従う」。 */
export type WorkflowLocality = NonNullable<ComfyWorkflowSummary['locality']>
/** 接続先を設定できる画像のプロバイダー。 */
export type LocalityProvider = keyof ProviderLocalitiesResponse

export function getProviderLocalities(): Promise<ProviderLocalitiesResponse> {
  return request('/api/settings/provider-localities')
}

/** 省略したプロバイダーは変えない。`null` は保存した値を消して既定に戻す。 */
export function updateProviderLocalities(body: ProviderLocalitiesUpdateRequest): Promise<ProviderLocalitiesResponse> {
  return request('/api/settings/provider-localities', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// -- Mod とフック(ADR-0051 7章。管理者のみ) -----------------------------------------

export type ModsResponse = components['schemas']['ModsResponse']
export type ModWebhookView = components['schemas']['ModWebhookView']
export type PythonModView = components['schemas']['PythonModView']
export type ModSettingFieldView = components['schemas']['ModSettingFieldView']
export type ModFilters = components['schemas']['ModFilters']
export type ModWebhookCreateRequest = components['schemas']['ModWebhookCreateRequest']
export type ModWebhookUpdateRequest = components['schemas']['ModWebhookUpdateRequest']
export type PythonModUpdateRequest = components['schemas']['PythonModUpdateRequest']
export type ModOrderItem = components['schemas']['ModOrderItem']
export type ModTestResponse = components['schemas']['ModTestResponse']
export type ModInvocationView = components['schemas']['ModInvocationView']
export type ModDeliveryView = components['schemas']['ModDeliveryView']
export type ModEvent = ModWebhookView['events'][number]
export type ModPurpose = NonNullable<ModFilters['purposes']>[number]
export type ModSource = NonNullable<ModFilters['sources']>[number]
export type ModOnFailure = ModWebhookView['on_failure']
export type ModKind = ModOrderItem['kind']
/** Asset・Run の判定(ADR-0051 4章・5章。Mod ごとの最新)。 */
export type ModVerdictView = components['schemas']['ModVerdictView']
/** Run の詳細の送る前のチェックの記録(`ai.before_send` だけ)。 */
export type RunModInvocationView = components['schemas']['RunModInvocationView']

export function getMods(): Promise<ModsResponse> {
  return request('/api/settings/mods')
}

export function updateMods(body: components['schemas']['ModsUpdateRequest']): Promise<ModsResponse> {
  return request('/api/settings/mods', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Webhook を足す。署名の鍵は必須で、応答には返らない(設定済みかどうかだけ)。 */
export function createModWebhook(body: ModWebhookCreateRequest): Promise<ModsResponse> {
  return request('/api/settings/mods/webhooks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** 書いた項目だけ変える。`secret` を送ると鍵を差し替える。 */
export function updateModWebhook(webhookId: string, body: ModWebhookUpdateRequest): Promise<ModsResponse> {
  return request(`/api/settings/mods/webhooks/${encodeURIComponent(webhookId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deleteModWebhook(webhookId: string): Promise<ModsResponse> {
  return request(`/api/settings/mods/webhooks/${encodeURIComponent(webhookId)}`, { method: 'DELETE' })
}

export function updatePythonMod(modId: string, body: PythonModUpdateRequest): Promise<ModsResponse> {
  return request(`/api/settings/mods/python/${encodeURIComponent(modId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** ダミーのイベントを送り、応答と所要時間を返す(記録には残らない)。Python の Mod は有効にしたものだけ。 */
export function testMod(kind: ModKind, modId: string, event: ModEvent): Promise<ModTestResponse> {
  const segment = kind === 'webhook' ? 'webhooks' : 'python'
  return request(`/api/settings/mods/${segment}/${encodeURIComponent(modId)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event }),
  })
}

/** 呼ぶ順番。この並びの順に呼ぶ。 */
export function updateModOrder(items: ModOrderItem[]): Promise<ModsResponse> {
  return request('/api/settings/mods/order', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
}

/** 直近の呼び出し(新しい順)。`failedOnly` は失敗・時間切れ・拒否だけ。 */
export function listModInvocations(params: { failedOnly?: boolean; limit?: number } = {}): Promise<{
  items: ModInvocationView[]
}> {
  const q = new URLSearchParams()
  if (params.failedOnly) q.set('failed_only', 'true')
  if (params.limit !== undefined) q.set('limit', String(params.limit))
  const qs = q.toString()
  return request(`/api/settings/mods/invocations${qs ? `?${qs}` : ''}`)
}

/** 配達の失敗(新しい順)。 */
export function listModDeliveryFailures(limit?: number): Promise<{ items: ModDeliveryView[] }> {
  const q = new URLSearchParams({ status: 'failed' })
  if (limit !== undefined) q.set('limit', String(limit))
  return request(`/api/settings/mods/deliveries?${q.toString()}`)
}

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

export type RunSummary = components['schemas']['RunSummary']
export type RunDetail = components['schemas']['RunDetail']
export type RunCreateRequest = components['schemas']['RunCreateRequest']
export type RunCreateResponse = components['schemas']['RunCreateResponse']
export type RunListResponse = components['schemas']['RunListResponse']
export type RunInputCreate = components['schemas']['RunInputCreate']
export type RunEvent = components['schemas']['RunEvent']
export type RunOutputRef = components['schemas']['RunOutputRef']
export type RunStatus = RunSummary['status']

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
export type ComfyParamType = ComfyExposedParam['type']

export type PriceEstimateResponse = components['schemas']['PriceEstimate']
export type PriceEstimateInputImage = components['schemas']['InputImageEstimateResponse']
export type PriceEstimateUnitPrices = components['schemas']['UnitPricesPer1M']
export type PriceEstimateUnavailableReason = NonNullable<PriceEstimateResponse['unavailable_reason']>

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** FastAPI の 422/4xx が返す `detail` を人が読める1つの文字列にする。 */
function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : null))
      .filter((msg): msg is string => msg !== null)
    if (messages.length > 0) return messages.join(' / ')
  }
  return null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // サーバーの文言(エラー、capabilities の見出し)を画面の言語に合わせる(ADR-0015)。
  const headers = new Headers(init?.headers)
  headers.set('Accept-Language', getLocale())
  const res = await fetch(path, { ...init, headers })
  if (!res.ok) {
    // oidc モードでセッションが切れた(ADR-0019)。`/api/auth/*` 自体の 401 は
    // 未ログイン時の正常応答(`/me` は 401 を返さないが、念のため対象から外す)。
    if (res.status === 401 && !path.startsWith('/api/auth/')) {
      markUnauthenticated()
    }
    let message = res.statusText || `HTTP ${res.status}`
    try {
      const body: unknown = await res.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        message = formatDetail((body as { detail: unknown }).detail) ?? message
      }
    } catch {
      // レスポンスが JSON でない場合はそのまま statusText を使う。
    }
    throw new ApiError(res.status, message)
  }
  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}

function toQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
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

/** `params.group_id` を渡すと、そのグループのメンバーだけに絞る(ADR-0022)。`kind` と併用できる。 */
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

/** up/down は 0〜10。省略時はサーバー既定(up=10, down=3)。 */
export function getAssetLineage(
  assetId: string,
  params: operations['get_asset_lineage']['parameters']['query'] = {},
): Promise<AssetLineageResponse> {
  return request(`/api/assets/${assetId}/lineage${toQuery(params)}`)
}

// -- グループ(ADR-0022) ------------------------------------------------------
// 階層なしのフラットなグループ。証跡ではないので更新・削除は自由(ADR-0003 の対象外)。

/** 削除済みでないグループを updated_at 降順で全件。ページングなし(prompt-sets と同じ)。 */
export function listAssetGroups(): Promise<AssetGroupListResponse> {
  return request('/api/asset-groups')
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

/** 論理削除。メンバー行は残るが、一覧・絞り込みからは消える。 */
export function deleteAssetGroup(groupId: string): Promise<void> {
  return request(`/api/asset-groups/${groupId}`, { method: 'DELETE' })
}

/** 既に入っているものは無視される。1〜200件。 */
export function addAssetsToGroup(groupId: string, assetIds: string[]): Promise<AssetGroupRow> {
  return request(`/api/asset-groups/${groupId}/assets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_ids: assetIds }),
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
export function setOpenAiBaseUrl(baseUrl: string): Promise<OpenAIBaseUrlStatus> {
  return request('/api/settings/openai-base-url', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_url: baseUrl }),
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

export function listComfyWorkflows(): Promise<ComfyWorkflowListResponse> {
  return request('/api/comfyui/workflows')
}

export function getComfyWorkflow(workflowId: string): Promise<ComfyWorkflowDetail> {
  return request(`/api/comfyui/workflows/${workflowId}`)
}

export function createComfyWorkflow(
  body: ComfyWorkflowCreateRequest,
): Promise<ComfyWorkflowDetail> {
  return request('/api/comfyui/workflows', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function updateComfyWorkflow(
  workflowId: string,
  body: ComfyWorkflowUpdateRequest,
): Promise<ComfyWorkflowDetail> {
  return request(`/api/comfyui/workflows/${workflowId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** 論理削除。過去の Run の記録(run.params.comfyui_prompt に確定済みのグラフを保存済み)は変わらない。 */
export function deleteComfyWorkflow(workflowId: string): Promise<void> {
  return request(`/api/comfyui/workflows/${workflowId}`, { method: 'DELETE' })
}

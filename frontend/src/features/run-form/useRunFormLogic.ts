/**
 * 生成・編集フォームの状態とロジック(見た目を持たない)。ADR-0009(2026-09-22 承認)で
 * スタジオが上段/下段レイアウトに変わったのに伴い、旧 `RunForm.tsx` から見た目を分離した。
 * `InputPane.tsx` がこれを使って下段(入力エリア)を描く。
 *
 * operation は利用者に選ばせない。入力画像(role=image)の枚数から `deriveOperation` で
 * 導出し、0枚なら generate、1枚以上なら edit として capabilities を引く。
 *
 * フォームの状態は RunFormContext(model/prompt/params/inputs)を「書き込み先」として
 * 常に反映するが、読み戻すのはマウント時の初期値としてだけにする(そうしないと、他ページからの
 * プリフィルと、このフォーム自身の書き込みが競合して無限ループになる)。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  createRun,
  getCapabilities,
  type CapabilitiesResponse,
  type ModelCapabilities,
  type OperationCapabilities,
  type AssetGroupRow,
  type ProviderEntry,
} from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { fmt, msg } from '../../i18n'
import { findProvider, isProviderModelValid } from '../../lib/capabilities'
import { deriveOperation } from './deriveOperation'
import { computeInitialFormValues } from './initialFormState'
import {
  UNSPECIFIED,
  buildParams,
  findDroppedParamNames,
  sanitizeRawValues,
  toRawParamValues,
  unspecifiedRawValue,
  withSizeParam,
  type RawParamValues,
} from './paramsBuilder'
import {
  paramToSizeState,
  roundSizeStateToMultiple,
  sizeStateForEditInputs,
  sizeStateForProvider,
  sizeToParam,
  validateSizeState,
  type SizeState,
} from './sizeValidation'
import { defsForMask, findIncompatibleViolations, isFieldEnabled } from './dependencies'
import { fillSeedDefaults } from './seedDefaults'
import { loadSeedMode } from './seedModePrefs'
import {
  inputCountRequirementMessage,
  isInputCountSatisfied,
  isMaskSatisfied,
  isMaskSupported,
  isOperationSupported,
  isProviderUsable,
  providerUnavailableMessage,
} from './submitChecks'
import { useDeletedInputAssetIds } from './useDeletedInputAssetIds'
import { computePromptInsertion, type PromptInsertMode } from './promptInsertion'
import { applyMention } from '../prompt-sets/mentionQuery'
import { useAssetGroups } from '../stock/groups/assetGroupQueries'
import { resolveAssetGroupId, withAssetGroupId } from './assetGroupSelection'
import { loadLastAssetGroupId } from '../../context/lastAssetGroupStorage'
import type { RunInputItem } from './types'
import {
  buildImportedRawParams,
  importTargetDefs,
  resolveImportedFormValues,
  toImportNotice,
  type ImportNotice,
} from '../sdwebui/importParams'
import { buildParameterSetRawParams, resolveParameterSetLoad } from '../parameter-sets/parameterSets'

export interface RunFormLogic {
  caps: CapabilitiesResponse | undefined
  capsLoading: boolean
  capsError: boolean

  /** 現在選ばれているプロバイダーの capabilities(未解決の間は undefined)。 */
  providerEntry: ProviderEntry | undefined

  provider: string
  model: string
  /** モデルを選ぶと provider も決まる(ADR-0013: `<optgroup>` はプロバイダー単位)。 */
  selectModel: (provider: string, model: string) => void
  prompt: string
  setPrompt: (prompt: string) => void
  /** 現在のプロンプトの文字数。 */
  promptLength: number
  promptMax: number
  /**
   * 系列インスペクターの「プロンプトに挿入/置き換え」用。textarea は制御コンポーネント
   * (`value={prompt}`)なので、DOM を直接書き換えても次の描画で元に戻る。必ずこれ経由で
   * ローカルの `prompt` state を更新する。
   */
  insertPrompt: (text: string, mode: PromptInsertMode, cursorPos: number | null) => void
  /**
   * `@` メンション確定用。`[start, end)` の範囲(`@query` の位置)を `text` で置き換える。
   * `insertPrompt` と同じく `pendingCursor` にカーソル位置(挿入直後)を積む。
   */
  replacePromptRange: (start: number, end: number, text: string) => void
  /**
   * 「新規生成」用。フォーム全体(prompt・provider・model・sizeState・rawParams)を
   * capabilities の初期値(`computeInitialFormValues`)に戻し、`droppedParamsNotice`・
   * `submitError` も消す。inputs はここでは触らない(AppBar が context 側を空にする。
   * ADR-0009「送信後のフォーム」2026-09-24 改訂)。
   */
  resetForm: () => void
  /** `insertPrompt` / `replacePromptRange` 後、state 反映(再描画)を待ってから textarea の
   * カーソル/フォーカスを合わせるための位置。適用したら `clearPendingCursor` で消す。 */
  pendingCursor: number | null
  clearPendingCursor: () => void

  rawParams: RawParamValues
  handleParamChange: (name: string, value: string) => void
  defs: import('../../api/client').ParamDef[]
  conditionalParams: import('../../api/client').ConditionalParam[]
  isFieldEnabledFor: (name: string) => boolean

  sizeState: SizeState
  /** 利用者がサイズ欄で変えたとき用(以後、入力画像を足してもサイズを自動では変えない)。 */
  setSizeState: (state: SizeState) => void

  /**
   * ADR-0022: 出力を入れるグループ。覚えている id をグループ一覧に照らして解決した値
   * (`resolveAssetGroupId`。削除済みなら null=「なし」)で、送信にもこの値を使う。
   */
  assetGroupId: string | null
  setAssetGroupId: (id: string | null) => void
  /** グループ一覧(`updated_at` の新しい順。未取得の間は空)。 */
  assetGroups: AssetGroupRow[]

  droppedParamsNotice: string | null
  /**
   * 画像の生成情報を読み込んだ後の「読み込めなかった項目」と注意(ADR-0038 9章)。閉じるか
   * 「新規生成」で消える。
   */
  importNotice: ImportNotice | null
  dismissImportNotice: () => void
  incompatibleErrors: string[]
  sizeValid: boolean
  sizeErrors: string[]

  operation: 'generate' | 'edit'
  /** 選んだモデルが今の operation(入力画像の枚数から導出)に対応していないか。 */
  operationSupported: boolean
  /** 選んだモデルが requires_mask で、マスクの入力が無いか。 */
  maskRequired: boolean
  hasMask: boolean
  /** 選んだモデルの edit がマスクの差し込み先を持つか(supports_mask)。ComfyUI の一部の
   * ワークフローは持たない。false のときはマスクを付けても送信できない。 */
  maskSupported: boolean
  /** 選んだプロバイダーが今使えるか(接続できない等)。使えなければ理由を持つ。 */
  providerAvailable: boolean
  providerUnavailableReason: string | null
  maxInputImages: number
  maxInputImageBytes: number
  maxMaskBytes: number
  imageInputCount: number
  hasDeletedInputs: boolean
  /**
   * 選んだモデルの edit が「ちょうどN枚」を要求するときの N(min_input_images === max_input_images
   * かつ 1 より大きいとき。ComfyUI の画像の枠)。それ以外(OpenAI/Fake 等、枚数に幅がある)は null。
   * 入力ストリップ付近の「入力画像 k / N 枚」表示に使う。
   */
  exactInputImagesRequired: number | null
  /** ちょうどの枚数が必要なモデルで枚数が足りないときのブロック理由(足りていれば null)。 */
  inputCountRequirementReason: string | null

  inputs: RunInputItem[]
  setInputs: (inputs: RunInputItem[]) => void

  canSubmit: boolean
  submitError: string | null
  isSubmitting: boolean
  submit: () => void
}

export function useRunFormLogic(
  onRunCreated: (runId: string) => void,
  onSubmitSuccess?: () => void,
): RunFormLogic {
  const queryClient = useQueryClient()
  const {
    formState,
    setFormState,
    pendingPromptInsert,
    clearPendingPromptInsert,
    pendingFormLoad,
    clearPendingFormLoad,
  } = useRunFormContext()

  // マウント時点の値だけを初期値として使う(以降 context から読み戻さない)。
  const initialRef = useRef(formState)

  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const caps = capsQuery.data

  const [provider, setProvider] = useState<string>(initialRef.current.provider)
  const [model, setModel] = useState<string>(initialRef.current.model)
  const [prompt, setPrompt] = useState<string>(initialRef.current.prompt)
  // ref は insertPrompt/replacePromptRange(useCallback([]) で identity を固定している)から
  // 最新値を読むために持つ。
  const promptRef = useRef(prompt)
  promptRef.current = prompt
  const [pendingCursor, setPendingCursor] = useState<number | null>(null)
  const [rawParams, setRawParams] = useState<RawParamValues>({})
  // パラメーターセットの読み込み(ADR-0040)で、今の seed の欄の値を引き継ぐために最新値を読む。
  const rawParamsRef = useRef(rawParams)
  rawParamsRef.current = rawParams
  const [sizeState, setSizeState] = useState<SizeState>(() =>
    paramToSizeState(initialRef.current.params.size),
  )
  // 利用者がサイズ欄で自分で変えたか。変えた後は、入力画像を足したときの自動の切り替え
  // (`sizeStateForEditInputs`)をしない。「新規生成」で戻す。
  const [sizeTouchedByUser, setSizeTouchedByUser] = useState(false)
  const setSizeStateByUser = useCallback((state: SizeState) => {
    setSizeTouchedByUser(true)
    setSizeState(state)
  }, [])
  // ADR-0022: 覚えているグループ id(削除済みかもしれない)。表示・送信には一覧で解決した
  // resolvedAssetGroupId を使い、ここは書き換えない(effect で掃除すると setState-in-effect になる)。
  const [assetGroupId, setAssetGroupId] = useState<string | null>(initialRef.current.assetGroupId ?? null)
  const assetGroupsQuery = useAssetGroups()
  // 一覧が未取得の間は undefined のままにする(resolveAssetGroupId が「確かめられない」と扱う)。
  const loadedAssetGroups = assetGroupsQuery.data ? (assetGroupsQuery.data.items ?? []) : undefined
  const assetGroups = loadedAssetGroups ?? []
  const resolvedAssetGroupId = resolveAssetGroupId(assetGroupId, loadedAssetGroups)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [droppedParamsNotice, setDroppedParamsNotice] = useState<string | null>(null)
  const [importNotice, setImportNotice] = useState<ImportNotice | null>(null)
  const didInitParamsRef = useRef(false)

  // operation は入力画像の枚数から導出する(利用者には選ばせない)。
  const operation = deriveOperation(formState.inputs)
  const hasMask = formState.inputs.some((i) => i.role === 'mask')

  // ADR-0013: capabilities はプロバイダーの一覧になった。まず今選んでいる provider の
  // capabilities(providerEntry)を引き、その中からモデルを探す。
  const providerEntry: ProviderEntry | undefined = findProvider(caps, provider)
  const modelCaps: ModelCapabilities | undefined = providerEntry?.models.find((m) => m.model === model)
  const opCaps: OperationCapabilities | undefined = modelCaps?.operations.find(
    (o) => o.operation === operation,
  )
  const defs = useMemo(() => opCaps?.params ?? [], [opCaps])

  // 入力画像パネルの上限は常に edit の枚数を使う(0枚の時点でも「追加できる」必要があるため、
  // 導出後の operation=generate の上限(0枚)を使ってはいけない)。
  const editOpCaps = modelCaps?.operations.find((o) => o.operation === 'edit')
  const maxInputImages = editOpCaps?.max_input_images ?? 0
  // ちょうどの枚数が必要なワークフロー(ComfyUI の画像の枠)だけ N を持つ。OpenAI/Fake のように
  // 1〜16枚の幅がある場合や、edit 自体が無いモデルでは null(表示側は通常の上限表示のままにする)。
  const exactInputImagesRequired =
    editOpCaps && editOpCaps.min_input_images > 1 && editOpCaps.min_input_images === editOpCaps.max_input_images
      ? editOpCaps.min_input_images
      : null

  // capabilities 読み込み後、provider/model が未選択、または選ばれているが現在の capabilities
  // に存在しない(localStorage から復元した値が古い・別環境のものだった、選んでいたプロバイダーが
  // 無効化された等)なら、capabilities の初期値(default_provider / default_model / default_size /
  // form_default)を適用する。
  useEffect(() => {
    if (!caps) return
    if (isProviderModelValid(caps, provider, model)) return
    const initial = computeInitialFormValues(caps)
    initialRef.current = {
      ...initialRef.current,
      provider: initial.provider,
      model: initial.model,
      params: initial.params,
    }
    setProvider(initial.provider)
    setModel(initial.model)
    setSizeState(paramToSizeState(initial.params.size))
  }, [caps, provider, model])

  // defs が初めて揃ったら初期値(プリフィル。localStorage からの復元や「同じ設定で再実行」等)
  // から rawParams を作る。sanitizeRawValues を通すことで、現在の capabilities に無いキー・
  // 選択肢(モデル切り替えや環境差で古くなった値)はここで落とす。続けて fillSeedDefaults で、
  // 記憶している seed モード(seedModePrefs、ワークフローに依存しない)が「固定」かつ値がまだ
  // 無い(新規フォーム、seed 付きモデル/ワークフローへの切り替え直後、seed の無い下書きの復元)
  // seed 欄に乱数を埋める。値が既にある(「同じ設定で再実行」で固定された seed、seed 入りの
  // 下書き)場合やモードが「ランダム」の場合は何もしない。
  useEffect(() => {
    if (defs.length === 0) return
    const seedMode = loadSeedMode()
    if (!didInitParamsRef.current) {
      const initialRaw = toRawParamValues(defs, initialRef.current.params)
      const sanitized = sanitizeRawValues(defs, initialRaw)
      const dropped = findDroppedParamNames(initialRaw, sanitized)
      if (dropped.length > 0) {
        const rf = msg().runForm.runFormLogic
        setDroppedParamsNotice(fmt(rf.droppedParamsSettings, { names: dropped.join(rf.listSeparator) }))
      }
      setRawParams(fillSeedDefaults(defs, sanitized, seedMode))
      didInitParamsRef.current = true
      return
    }
    setRawParams((prevRawParams) => {
      const next = sanitizeRawValues(defs, prevRawParams)
      const dropped = findDroppedParamNames(prevRawParams, next)
      if (dropped.length > 0) {
        const rf = msg().runForm.runFormLogic
        setDroppedParamsNotice(fmt(rf.droppedParamsInputs, { names: dropped.join(rf.listSeparator) }))
      } else {
        setDroppedParamsNotice(null)
      }
      return fillSeedDefaults(defs, next, seedMode)
    })
    // defs は model/operation が変わった時だけ中身が変わる想定。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defs])

  // 依存条件(conditional_params)で無効になったフィールドは値を未指定に戻す。
  useEffect(() => {
    if (!providerEntry) return
    setRawParams((prev) => {
      let changed = false
      const next: RawParamValues = { ...prev }
      for (const def of defs) {
        const enabled = isFieldEnabled(defs, prev, providerEntry.conditional_params ?? [], def.name)
        if (!enabled && prev[def.name] !== undefined && prev[def.name] !== UNSPECIFIED && prev[def.name] !== '') {
          next[def.name] = unspecifiedRawValue(def.type)
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [providerEntry, defs, rawParams])

  // ローカルの状態を context へ書き込む(他ページから読める最新値にしておく)。inputs は
  // このフックでは触らず、setInputs 経由で呼び出し側が直接 context に書き込む。
  // マスクが無いときは、マスクがあるときだけ意味を持つ項目(mask_only)を送らない。値は rawParams に
  // 残すので、マスクを描き直せば元の値に戻る。
  useEffect(() => {
    const params = withSizeParam(buildParams(defsForMask(defs, hasMask), rawParams), sizeToParam(sizeState))
    setFormState({ provider, model, prompt, params, inputs: formState.inputs, assetGroupId })
    // formState.inputs はここでは変更しないので依存に含めない(無限ループ回避)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, model, prompt, rawParams, sizeState, defs, assetGroupId, hasMask])

  const incompatibleErrors = providerEntry
    ? findIncompatibleViolations(defs, rawParams, providerEntry.incompatible_pairs ?? [])
    : []
  // 16の倍数への丸めは blur・送信時に自動で行われるため、検証も「丸めた後」の値に対して行う
  // (丸めれば解消される「16の倍数でない」エラーを、丸める前の一時的な入力値で出さないため)。
  // size が null のプロバイダー(ComfyUI 等)は size パラメータを取らない(SizeInput 自体を
  // 呼び出し側が隠す)ので、常に有効とする。
  const sizeValidation = providerEntry?.size
    ? validateSizeState(providerEntry.size, roundSizeStateToMultiple(providerEntry.size, sizeState))
    : { valid: true, errors: [] as string[] }
  const promptLength = prompt.length
  const promptMax = providerEntry?.prompt_max_length ?? 32_000
  const promptValid = prompt.length > 0 && prompt.length <= promptMax

  const imageInputCount = formState.inputs.filter((i) => i.role === 'image').length
  // 入力画像を足した(0 枚 → 1 枚以上)とき、auto を受け付けないプロバイダー(SD WebUI)で
  // サイズが既定の幅×高さのままなら「未指定」にし、サーバーに入力画像の寸法から決めさせる
  // (ADR-0038 2章)。描画中に前回の値と比べる(effect で setState しない)。初期表示の入力画像
  // (「同じ設定で再実行」など)は「足した」に当たらないので、記録のサイズを変えない。
  const hasImageInputs = imageInputCount > 0
  const [prevHasImageInputs, setPrevHasImageInputs] = useState(hasImageInputs)
  if (hasImageInputs !== prevHasImageInputs) {
    setPrevHasImageInputs(hasImageInputs)
    if (hasImageInputs) {
      const next = sizeStateForEditInputs(providerEntry?.size, sizeState, sizeTouchedByUser)
      if (next) setSizeState(next)
    }
  }
  const editInputsValid = imageInputCount <= maxInputImages
  const inputCountRequirementReason = inputCountRequirementMessage(opCaps, operation, imageInputCount)
  const deletedInputAssetIds = useDeletedInputAssetIds(formState.inputs)
  const hasDeletedInputs = deletedInputAssetIds.length > 0

  const operationSupported = isOperationSupported(opCaps)
  const maskRequired = opCaps?.requires_mask ?? false
  // opCaps 未解決(読み込み中)の間は妨げない(operationSupported 側で別途止まる)。
  const maskSupported = opCaps?.supports_mask ?? true
  const providerAvailable = isProviderUsable(providerEntry)
  const providerUnavailableReason = providerUnavailableMessage(providerEntry)

  function setInputs(next: RunInputItem[]) {
    setFormState({ ...formState, inputs: next })
  }

  // ref 経由で最新の prompt を読むことで、依存配列を空にして identity を安定させる
  // (呼び出し側(ResultPane)が毎打鍵で再レンダーされないように)。
  const insertPrompt = useCallback((text: string, mode: PromptInsertMode, cursorPos: number | null) => {
    const result = computePromptInsertion(promptRef.current, text, mode, cursorPos)
    setPrompt(result.text)
    setPendingCursor(result.cursor)
  }, [])

  const replacePromptRange = useCallback((start: number, end: number, text: string) => {
    const result = applyMention(promptRef.current, { start, end }, text)
    setPrompt(result.text)
    setPendingCursor(result.cursor)
  }, [])

  const clearPendingCursor = useCallback(() => setPendingCursor(null), [])

  // 「新規生成」用。フォーム全体(provider・model・sizeState・rawParams)を capabilities の
  // 初期値(`computeInitialFormValues`)に戻す(ADR-0009「送信後のフォーム」2026-09-24 改訂)。
  // inputs は AppBar が context 側を直接空にするので、ここでは触らない。caps が未取得
  // (読み込み中に押された場合)なら prompt/エラー類だけ空にしておき、後段(「provider/model
  // 未選択なら初期値を適用する」既存 effect)に委ねる。
  // seed(widget: 'seed')の欄は、defs が変わらない(同じ provider/model に戻る)場合は
  // 「defs が初めて揃ったら初期値を作る」effect が再実行されないため、ここで
  // fillSeedDefaults を直接呼んで記憶している seed モードを反映する。
  function resetForm() {
    setPrompt('')
    // グループは初期値(なし)ではなく、最後に選んだグループに戻す(ADR-0022。削除済みなら
    // resolveAssetGroupId が「なし」にする)。
    setAssetGroupId(loadLastAssetGroupId())
    setSubmitError(null)
    setDroppedParamsNotice(null)
    setImportNotice(null)
    if (!caps) return
    const initial = computeInitialFormValues(caps)
    const initialProviderEntry = findProvider(caps, initial.provider)
    const initialModelCaps = initialProviderEntry?.models.find((m) => m.model === initial.model)
    const initialGenerateDefs =
      initialModelCaps?.operations.find((o) => o.operation === 'generate')?.params ?? []
    const initialRaw = sanitizeRawValues(initialGenerateDefs, toRawParamValues(initialGenerateDefs, initial.params))
    initialRef.current = {
      ...initialRef.current,
      provider: initial.provider,
      model: initial.model,
      params: initial.params,
    }
    setProvider(initial.provider)
    setModel(initial.model)
    setSizeState(paramToSizeState(initial.params.size))
    setSizeTouchedByUser(false)
    setRawParams(fillSeedDefaults(initialGenerateDefs, initialRaw, loadSeedMode()))
  }

  // スタジオの外からの挿入・置き換えのリクエスト(サイドバーのプロンプトセットの「末尾に追加」、
  // ビューア・Run 詳細の「最終プロンプト」の挿入・置き換え)を消費する。スタジオが未マウント
  // の間はリクエストが context に残り、マウント後(この effect が初めて走るのは、
  // 同じ render で上の useState 初期化(initialRef からの prompt 復元)が終わった後)に反映する。
  // nonce で同じリクエストの二重消費を防ぐ。
  const consumedInsertNonceRef = useRef<number | null>(null)
  useEffect(() => {
    if (!pendingPromptInsert) return
    if (consumedInsertNonceRef.current === pendingPromptInsert.nonce) return
    consumedInsertNonceRef.current = pendingPromptInsert.nonce
    insertPrompt(pendingPromptInsert.text, pendingPromptInsert.mode, null)
    clearPendingPromptInsert()
  }, [pendingPromptInsert, insertPrompt, clearPendingPromptInsert])

  // フォームへの読み込み。画像の生成情報から SD WebUI のフォームへ(ADR-0038 9章)と、
  // パラメーターセットから(ADR-0040)。プロンプトの置き換えの確認は積む側(読み込みのダイアログ、
  // ビューア、サイドバー)で済んでいる。capabilities が揃うまで待つ。パラメーターは、入れる先の
  // モデル・操作の定義でここで作る(同じモデルのままでは defs の effect が走らないため。
  // 「新規生成」の resetForm と同じやり方)。入力画像は変えない。
  const consumedFormLoadNonceRef = useRef<number | null>(null)
  useEffect(() => {
    if (!pendingFormLoad || !caps) return
    if (consumedFormLoadNonceRef.current === pendingFormLoad.nonce) return
    consumedFormLoadNonceRef.current = pendingFormLoad.nonce
    clearPendingFormLoad()
    const request = pendingFormLoad.request

    let next: {
      provider: string
      model: string
      prompt: string | null
      sizeState: SizeState
      rawParams: RawParamValues
      params: Record<string, string | number | boolean>
      notice: ImportNotice
    }
    if (request.kind === 'sdwebui') {
      const values = resolveImportedFormValues(request.response, caps, { provider, model })
      if (!values) {
        setSubmitError(msg().sdwebui.importParams.noModels)
        return
      }
      const targetDefs = importTargetDefs(caps, values, operation)
      next = {
        provider: values.provider,
        model: values.model,
        prompt: values.prompt,
        sizeState: paramToSizeState(values.size),
        rawParams: buildImportedRawParams(targetDefs, values.params, loadSeedMode()),
        params: values.params,
        notice: toImportNotice(request.response),
      }
    } else {
      const resolved = resolveParameterSetLoad(request.set, caps, { provider, model }, operation)
      if (!resolved.ok) {
        setSubmitError(resolved.reason)
        return
      }
      next = {
        provider: resolved.provider,
        model: resolved.model,
        prompt: resolved.prompt,
        sizeState: resolved.sizeState,
        rawParams: buildParameterSetRawParams(
          resolved.targetDefs,
          resolved.params,
          rawParamsRef.current,
          loadSeedMode(),
        ),
        params: resolved.params,
        notice: resolved.notice,
      }
    }

    initialRef.current = {
      ...initialRef.current,
      provider: next.provider,
      model: next.model,
      prompt: next.prompt ?? initialRef.current.prompt,
      params: next.params,
    }
    didInitParamsRef.current = true
    setProvider(next.provider)
    setModel(next.model)
    // null はプロンプトを保存していないパラメーターセット(今のプロンプトのまま)。
    if (next.prompt !== null) setPrompt(next.prompt)
    setSizeState(next.sizeState)
    // 読み込んだサイズは意図したものなので、入力画像を足しても自動では変えない。
    setSizeTouchedByUser(true)
    setRawParams(next.rawParams)
    setSubmitError(null)
    setDroppedParamsNotice(null)
    setImportNotice(next.notice)
    // provider・model・operation は消費した時点の値だけを使う(変わるたびに走らせない)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingFormLoad, caps, clearPendingFormLoad])

  const mutation = useMutation({
    mutationFn: createRun,
    onSuccess: (res) => {
      setSubmitError(null)
      // 送信してもフォームは何も変えない(ADR-0009「送信後のフォーム」2026-09-24 改訂)。
      // prompt・inputs(マスク含む)・provider・model・params はそのまま残す。
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      onRunCreated(res.id)
      onSubmitSuccess?.()
    },
    onError: (err: unknown) => {
      setSubmitError(err instanceof ApiError ? err.message : msg().runForm.runFormLogic.submitFailedDefault)
    },
  })

  function handleParamChange(name: string, value: string) {
    setRawParams((prev) => ({ ...prev, [name]: value }))
  }

  function selectModel(nextProvider: string, nextModel: string) {
    // プロバイダーをまたいで切り替えたとき、今のサイズが新しいプロバイダーで使えなければ
    // (OpenAI の auto や 4K を、auto を受け付けず長辺 2048px の SD WebUI へ持ち込むなど)、
    // そのプロバイダーの既定のサイズに戻す。
    if (nextProvider !== provider) {
      const nextEntry = findProvider(caps, nextProvider)
      if (nextEntry?.size) {
        const constraints = nextEntry.size
        setSizeState((prev) => {
          const next = sizeStateForProvider(constraints, nextEntry.default_size, prev)
          // 入力画像があるまま SD WebUI へ切り替えたときも、入力画像を足したときと同じにする。
          if (imageInputCount === 0) return next
          return sizeStateForEditInputs(constraints, next, sizeTouchedByUser) ?? next
        })
      }
    }
    setProvider(nextProvider)
    setModel(nextModel)
  }

  const canSubmit =
    promptValid &&
    sizeValidation.valid &&
    incompatibleErrors.length === 0 &&
    editInputsValid &&
    isInputCountSatisfied(opCaps, operation, imageInputCount) &&
    !hasDeletedInputs &&
    provider !== '' &&
    model !== '' &&
    operationSupported &&
    isMaskSatisfied(opCaps, hasMask) &&
    isMaskSupported(opCaps, hasMask) &&
    providerAvailable &&
    !mutation.isPending

  function submit() {
    if (!canSubmit) return
    // size が無いプロバイダーは常に未指定(送らない)。送信直前に16の倍数へ丸める
    // (run.params.size には丸めた後の値を入れる)。
    const roundedSizeState = providerEntry?.size
      ? roundSizeStateToMultiple(providerEntry.size, sizeState)
      : sizeState
    const sizeParam = providerEntry?.size ? sizeToParam(roundedSizeState) : undefined
    const params = withSizeParam(buildParams(defsForMask(defs, hasMask), rawParams), sizeParam)
    const inputs =
      operation === 'edit'
        ? formState.inputs.map((i) => ({ asset_id: i.assetId, role: i.role, position: i.position }))
        : []
    mutation.mutate(withAssetGroupId({ operation, model, prompt, provider, params, inputs }, resolvedAssetGroupId))
  }

  return {
    caps,
    capsLoading: capsQuery.isLoading,
    capsError: capsQuery.isError,
    providerEntry,
    provider,
    model,
    selectModel,
    prompt,
    setPrompt,
    promptLength,
    promptMax,
    insertPrompt,
    replacePromptRange,
    resetForm,
    pendingCursor,
    clearPendingCursor,
    rawParams,
    handleParamChange,
    defs,
    conditionalParams: providerEntry?.conditional_params ?? [],
    isFieldEnabledFor: (name: string) =>
      isFieldEnabled(defs, rawParams, providerEntry?.conditional_params ?? [], name, { hasMask }),
    sizeState,
    setSizeState: setSizeStateByUser,
    assetGroupId: resolvedAssetGroupId,
    setAssetGroupId,
    assetGroups,
    droppedParamsNotice,
    importNotice,
    dismissImportNotice: () => setImportNotice(null),
    incompatibleErrors,
    sizeValid: sizeValidation.valid,
    sizeErrors: sizeValidation.errors,
    operation,
    operationSupported,
    maskRequired,
    hasMask,
    maskSupported,
    providerAvailable,
    providerUnavailableReason,
    maxInputImages,
    maxInputImageBytes: providerEntry?.max_input_image_bytes ?? 50 * 1024 * 1024,
    maxMaskBytes: providerEntry?.max_mask_bytes ?? 10 * 1024 * 1024,
    imageInputCount,
    hasDeletedInputs,
    exactInputImagesRequired,
    inputCountRequirementReason,
    inputs: formState.inputs,
    setInputs,
    canSubmit,
    submitError,
    isSubmitting: mutation.isPending,
    submit,
  }
}

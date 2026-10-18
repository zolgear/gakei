/**
 * `/settings/comfyui/workflows/new` と `/settings/comfyui/workflows/:id`(ADR-0013、ADR-0031、ADR-0052)。
 * 設定の枠(目次とヘッダー)の中で開き、目次は ComfyUI を選んだ状態にする(`pages/SettingsPage.tsx`)。
 *
 * ComfyUI の「Export (API)」で書き出した JSON を選ぶと、analyze の読み取りを初期値にする。
 * 差し込み先はカードで見せる(ADR-0052 4章):
 * - 読み取りの要約、決めきれない項目の問いのカード、検証の一覧、1項目1行の差し込み先の行。
 * - 行は値・理由の一文・確信度のバッジを出し、開くと道筋と行内のピッカーが出る。
 * - 今までの2段のドロップダウンは「詳細設定」に畳んで残す(保存の形式は変えない)。
 * - 公開パラメーターは3段(推奨 / その他 / 公開しない)。
 * - 検証の規則はサーバーにだけある。値を変えるたびに検証の API(保存しない)を呼び、結果を出す。
 *   エラーは保存を止め、警告は画面内の確認を経て残したまま保存できる。
 * - 保存後は、生成画面で試すへ誘導する(検証だけの実行は作らない)。
 * - アシスタントが使えて接続先が Structured Outputs に対応するときだけ、読み取りの要約の下に
 *   「アシスタントで推定」を出す(ADR-0052 6章。`AssistantSuggestPanel`)。採った項目は「手で選択」。
 * 編集時は保存済みの bindings/exposed_params を初期値にし(analyze の提案では上書きしない)、
 * 今の読み取りと違う行に印を付ける(自動では書き換えない)。
 *
 * 「保存で反映」のページとして扱う(ADR-0031 2〜4章)。保存はヘッダーの1か所。保存していない
 * 変更があれば、離れる前に確かめる(`SettingsPageFrame` の `UnsavedChangesGuard`)。
 * 新規登録の保存に成功したら、そのワークフローの編集画面へ移り、保存後のカードを出す。
 *
 * 未接続(`enabled === false`)でも登録・編集はできるが、analyze の `/object_info` 補完が
 * 効かず、保存してもモデルの選択肢には出ないので、その旨を警告として出す(ADR-0013 7章)。
 */
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent as ReactDragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, type Location, type NavigateFunction } from 'react-router'
import {
  ApiError,
  analyzeComfyWorkflowText,
  analyzeSavedComfyWorkflow,
  createComfyWorkflow,
  getAssistantSettings,
  getComfyUIStatus,
  getComfyWorkflow,
  updateComfyWorkflow,
  validateComfyWorkflow,
  type ComfyBindingCandidate,
  type ComfyBindingReading,
  type ComfyOperation,
  type ComfySuggestResponse,
  type ComfyValidationIssue,
} from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useIsMobileViewport } from '../../lib/viewport'
import { fmt, useI18n, type Messages } from '../../i18n'
import { canSaveDraft } from '../settings/settingsDraft'
import { SettingsPageFrame } from '../settings/SettingsPageFrame'
import { WorkflowLocalitySelect } from '../settings/LocalityParts'
import { SettingsSection } from '../settings/SettingsParts'
import { ASSISTANT_SETTINGS_QUERY_KEY } from '../settings/queryKeys'
import { useSettingsShell } from '../settings/settingsShell'
import type { SettingsDraftControls } from '../settings/useSettingsDraft'
import settingsStyles from '../settings/settings.module.css'
import { moveImageSlot, type BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'
import { validateWorkflowForm } from './workflowFormValidation'
import { buildWorkflowCreateRequest, buildWorkflowUpdateRequest } from './workflowSave'
import { formDisconnectedNotice } from './comfyuiConnectionForm'
import {
  COMFYUI_SETTINGS_PATH,
  EMPTY_WORKFLOW_FORM,
  applyAnalyzedTemplate,
  changedWorkflowFormKeys,
  isOpenedFromComfyUIPage,
  withOperation,
  workflowFormBackDestination,
  workflowFormFromSaved,
  workflowFormPath,
  type WorkflowFormDraftKey,
  type WorkflowFormValues,
} from './workflowFormDraft'
import {
  FIELD_ORDER,
  applyAnswer,
  bindingRowId,
  questionCardId,
  buildValidateRequest,
  savedComparison,
  effectiveExposedRows,
  enableRecommendedRows,
  fieldIsSet,
  generalWarningMessages,
  initialOrigins,
  initiallyExpanded,
  isJustSaved,
  originsAfterChange,
  originsAfterReplace,
  pendingQuestions,
  readingsByField,
  splitIssues,
  summarizeRows,
  visibleFields,
  withReadingValue,
  type CardAnalysis,
  type FieldKey,
  type IssueTarget,
  type PendingQuestion,
  type RowOrigins,
} from './bindingCards'
import { BindingRows } from './BindingRows'
import { BindingQuestionCard } from './BindingQuestionCard'
import { BindingIssues } from './BindingIssues'
import { ReadingSummary } from './ReadingSummary'
import { ExposedParamsTiers } from './ExposedParamsTiers'
import { BindingMap } from './BindingMap'
import { BindingMapInspector } from './BindingMapInspector'
import {
  assignInputRole,
  mapEdges,
  setInputExposed,
  toggleFinalPrompt,
  toggleOutput,
  withoutInputRef,
  type InputChoice,
} from './bindingGraph'
import { WorkflowSavedCard } from './WorkflowSavedCard'
import { AssistantSuggestPanel } from './AssistantSuggestPanel'
import { adoptSuggestion } from './assistantSuggest'
import { NodeInputPicker } from './NodeInputPicker'
import { NodeMultiPicker } from './NodeMultiPicker'
import { NodeSelectPicker } from './NodeSelectPicker'
import styles from './ComfyUIWorkflowFormPage.module.css'

function operationOptions(t: Messages): { value: ComfyOperation; label: string }[] {
  return [
    { value: 'generate', label: t.comfyui.form.generateOption },
    { value: 'edit', label: t.comfyui.form.editOption },
  ]
}

/** ComfyUI のページへ戻る(そこから開いたなら1つ戻り、そうでなければ置き換える)。 */
function goToComfyUIPage(navigate: NavigateFunction, location: Pick<Location, 'state' | 'key'>) {
  const dest = workflowFormBackDestination({
    openedFromComfyUIPage: isOpenedFromComfyUIPage(location.state),
    locationKey: location.key,
  })
  if (dest.type === 'back') void navigate(-1)
  else void navigate(dest.path, { replace: true })
}

/** 差し込み先の表示(一覧 / 図)を覚えておく localStorage のキー。 */
const VIEW_STORAGE_KEY = 'gakei.comfyui.workflowBindingView'

type BindingView = 'list' | 'map'
type PanelTab = 'bindings' | 'node'

function loadBindingView(): BindingView {
  try {
    return window.localStorage.getItem(VIEW_STORAGE_KEY) === 'map' ? 'map' : 'list'
  } catch {
    return 'list'
  }
}

function storeBindingView(view: BindingView) {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, view)
  } catch {
    // 覚えられなくても表示は切り替わる。
  }
}

/** 読み取りが無いときの図の読み取り(毎回新しい配列を作らない)。 */
const NO_READINGS: ComfyBindingReading[] = []

/** 画面の値が変わったら検証の API を呼び直すまでの待ち(ミリ秒)。 */
const VALIDATE_DEBOUNCE_MS = 350

interface ComfyUIWorkflowFormPageProps {
  /** 編集するワークフロー。省くと新規登録。 */
  workflowId?: string
}

export function ComfyUIWorkflowFormPage({ workflowId }: ComfyUIWorkflowFormPageProps) {
  const { t } = useI18n()
  const f = t.comfyui.form
  const c = t.comfyui.cards
  const isEdit = workflowId !== undefined
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useSettingsShell()
  const { formState, setFormState, requestFormLoad } = useRunFormContext()
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const workflowQuery = useQuery({
    queryKey: ['comfyui-workflow', workflowId],
    queryFn: () => getComfyWorkflow(workflowId as string),
    enabled: isEdit,
  })

  const statusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })
  const disconnectedNotice = statusQuery.data ? formDisconnectedNotice(statusQuery.data.enabled) : null
  // アシスタントでの推定(ADR-0052 6章)は、アシスタントが使えて、接続先が Structured Outputs に
  // 対応するときだけ出す。
  const assistantQuery = useQuery({ queryKey: ASSISTANT_SETTINGS_QUERY_KEY, queryFn: getAssistantSettings })
  const canSuggest = Boolean(assistantQuery.data?.usable && assistantQuery.data?.structured_outputs)

  // 保存済みの値(編集画面では読み込みと analyze が済むまで null)と、画面の値。
  const [saved, setSaved] = useState<WorkflowFormValues | null>(isEdit ? null : EMPTY_WORKFLOW_FORM)
  const [values, setValues] = useState<WorkflowFormValues>(EMPTY_WORKFLOW_FORM)
  // 読み取り(analyze の結果のうち、カードが使う部分)。読み取りに失敗したら null。
  const [analysis, setAnalysis] = useState<CardAnalysis | null>(null)
  const [origins, setOrigins] = useState<RowOrigins>(() => initialOrigins(isEdit ? 'saved' : 'reading'))
  const [expanded, setExpanded] = useState<ReadonlySet<FieldKey>>(new Set())
  const [flashField, setFlashField] = useState<FieldKey | null>(null)
  // 図(ADR-0052 4章): 道筋を強調する項目、「図で選ぶ」の項目、選んでいるノードか束。
  const [mapFocus, setMapFocus] = useState<FieldKey | null>(null)
  const [pickField, setPickField] = useState<FieldKey | null>(null)
  const [mapSelected, setMapSelected] = useState<string | null>(null)
  const isMobile = useIsMobileViewport()
  // 差し込み先の表示: 一覧(カード)か図か。どちらも同じ状態(差し込み先・公開パラメーター・検証)を使う。
  const [bindingView, setBindingView] = useState<BindingView>(loadBindingView)
  // 図の表示の右のパネルのタブ。
  const [panelTab, setPanelTab] = useState<PanelTab>('bindings')
  // サーバーの検証の結果(エラーと警告)。値を変えるたびに取り直す。
  const [issues, setIssues] = useState<ComfyValidationIssue[]>([])
  const [confirming, setConfirming] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  // 保存後のカードを出すか(新規登録は編集画面へ移ってから出す)。
  const [showSavedCard, setShowSavedCard] = useState(() => isEdit && isJustSaved(location.state))
  // 新規登録の保存に成功したら、そのワークフローの編集画面へ移る(変更が無くなったのを見届けてから)。
  const [createdId, setCreatedId] = useState<string | null>(null)
  const movedRef = useRef(false)

  // 編集画面: 保存済みのワークフローを読み込んだら、そのテンプレートで analyze を呼び直して
  // ノードの選択肢と今の読み取りを作る。フォームの初期値には保存済みの bindings/exposed_params を使う。
  // analyze に失敗しても保存済みの値は残す(選択肢が無いだけ)。
  useEffect(() => {
    if (!isEdit || !workflowQuery.data || saved !== null) return
    const wf = workflowQuery.data
    let cancelled = false
    // 保存したテンプレートをサーバーで読む(ブラウザで読み直すと 1.0 が 1 になり、型が変わるため)。
    analyzeSavedComfyWorkflow(workflowId as string)
      .then((result) => {
        if (cancelled) return
        const initial = workflowFormFromSaved(wf, result)
        setSaved(initial)
        setValues(initial)
        setAnalysis(result)
        setIssues(wf.validation_issues ?? [])
        // 保存済みの値が今の読み取りと違う行は開いておく(古い推定で保存した誤りに気付く入口)。
        const savedReadings = readingsByField(result.readings)
        const differing = visibleFields(wf.operation).filter(
          (field) => savedComparison(initial.bindings, result.suggested_bindings, savedReadings[field], field) === 'differ',
        )
        setExpanded(new Set(differing))
        setMapFocus(differing[0] ?? null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const initial = workflowFormFromSaved(wf, null)
        setSaved(initial)
        setValues(initial)
        setIssues(wf.validation_issues ?? [])
        setFileError(err instanceof ApiError ? err.message : f.templateParseFailed)
      })
    return () => {
      cancelled = true
    }
  }, [isEdit, workflowId, workflowQuery.data, saved, f.templateParseFailed])

  // ファイルの JSON の文字列のまま読み取る(`1.0` を FLOAT のまま推定させる)。
  const analyzeMutation = useMutation({ mutationFn: analyzeComfyWorkflowText })

  const saveMutation = useMutation({
    mutationFn: (input: WorkflowFormValues) => {
      if (input.template === null) throw new Error('template is not set')
      const args = {
        ...input,
        template: input.template,
        exposedRows: effectiveExposedRows(input.exposedRows, input.bindings),
      }
      if (!isEdit) return createComfyWorkflow(buildWorkflowCreateRequest(args), input.templateText)
      // テンプレートを差し替えていなければ送らない(保存済みのテンプレートの数の型を保つ)。
      const templateChanged = saved === null || saved.template !== input.template
      const body = buildWorkflowUpdateRequest(args)
      if (!templateChanged) delete body.template
      return updateComfyWorkflow(workflowId as string, body, templateChanged ? input.templateText : null)
    },
    onSuccess: (result, input) => {
      queryClient.invalidateQueries({ queryKey: ['capabilities'] })
      queryClient.invalidateQueries({ queryKey: ['comfyui-workflows'] })
      if (isEdit) queryClient.invalidateQueries({ queryKey: ['comfyui-workflow', workflowId] })
      toast.show({ message: t.settings.frame.savedToast })
      setSaved(input)
      setConfirming(false)
      setIssues(result.validation_issues ?? [])
      if (isEdit) {
        setShowSavedCard(true)
        window.scrollTo?.({ top: 0 })
      } else {
        setCreatedId(result.id)
      }
    },
  })
  const resetSaveError = saveMutation.reset

  const changedKeys = saved ? changedWorkflowFormKeys(saved, values) : []
  const diagramEdges = useMemo(() => mapEdges(values.nodes, analysis?.edges), [values.nodes, analysis])
  const dirty = changedKeys.length > 0
  const { name, operation, template, nodes, bindings, exposedRows } = values
  const effectiveRows = useMemo(() => effectiveExposedRows(exposedRows, bindings), [exposedRows, bindings])
  const clientErrors = validateWorkflowForm({
    name,
    operation,
    hasTemplate: template !== null,
    bindings,
    exposedRows: effectiveRows,
  })
  const fields = visibleFields(operation)
  const readings = readingsByField(analysis?.readings)
  const questions: PendingQuestion[] = pendingQuestions(fields, readings, origins)
  const { errors: serverErrors, warnings: serverWarnings } = splitIssues(issues)
  // 問いが残っている項目の「未設定」は、問いのカードと同じことなので一覧には重ねない。
  const clientErrorsShown = clientErrors.filter(
    (message) =>
      !(message === t.comfyui.bindingsValidation.promptRequired && questions.some((q) => q.id === 'prompt')),
  )
  const blockerCount = clientErrorsShown.length + serverErrors.length + questions.length
  const canSave = canSaveDraft({
    changedCount: changedKeys.length,
    valid: blockerCount === 0,
    saving: saveMutation.isPending,
  })

  // 値が変わったら、保存せずに検証だけを掛ける(規則はサーバーにだけある。ADR-0052 3章)。
  // validateKey が operation・bindings・公開パラメーターの中身を表す。
  const validateKey = template === null ? null : JSON.stringify([operation, bindings, effectiveRows])
  useEffect(() => {
    if (template === null || validateKey === null) return
    let cancelled = false
    const [op, b, rows] = JSON.parse(validateKey) as [ComfyOperation, BindingsFormState, ExposedParamRow[]]
    const timer = window.setTimeout(() => {
      validateComfyWorkflow(buildValidateRequest({ template, operation: op, bindings: b, exposedRows: rows }))
        .then((result) => {
          if (!cancelled) setIssues(result.issues ?? [])
        })
        .catch(() => {
          // 検証に失敗しても前の結果を残す(保存のときにサーバーがもう一度検証する)。
        })
    }, VALIDATE_DEBOUNCE_MS)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [template, validateKey])

  // 新規登録の保存のあと、保存した値が保存済みになってから編集画面へ移る(移動の確認が出ないように)。
  useEffect(() => {
    if (createdId === null || dirty || movedRef.current) return
    movedRef.current = true
    const state = typeof location.state === 'object' && location.state !== null ? location.state : {}
    void navigate(workflowFormPath(createdId), { replace: true, state: { ...state, justSaved: true } })
  }, [createdId, dirty, navigate, location.state])

  // 強調は少しの間だけ。
  useEffect(() => {
    if (flashField === null) return
    const timer = window.setTimeout(() => setFlashField(null), 1500)
    return () => window.clearTimeout(timer)
  }, [flashField])

  function saveNow() {
    if (canSave) saveMutation.mutate(values)
  }

  const draft: SettingsDraftControls = {
    changedCount: changedKeys.length,
    dirty,
    canSave,
    saving: saveMutation.isPending,
    saveError: saveMutation.error
      ? saveMutation.error instanceof ApiError
        ? saveMutation.error.message
        : t.settings.frame.saveFailed
      : null,
    save: () => {
      if (!canSave) return
      // 警告が残っているときは、画面内の帯で確かめてから保存する(ブラウザの confirm は使わない)。
      if (serverWarnings.length > 0 && !confirming) {
        setConfirming(true)
        focusElement('binding-issues')
        return
      }
      saveNow()
    },
    reset: () => {
      if (saved) setValues(saved)
      setOrigins(initialOrigins(isEdit ? 'saved' : 'reading'))
      setWarnings([])
      setNotice(null)
      setFileError(null)
      setConfirming(false)
      resetSaveError()
    },
  }

  /** 値を変える。前回の保存の失敗表示と、警告の確認の帯は消す。 */
  function change(update: (prev: WorkflowFormValues) => WorkflowFormValues) {
    setValues(update)
    setConfirming(false)
    setShowSavedCard(false)
    resetSaveError()
  }
  /** 差し込み先を手で変える。値が変わった項目と触った項目の出どころを「手で選択」にする。 */
  function changeBindings(update: (prev: BindingsFormState) => BindingsFormState, touched: FieldKey[] = []) {
    const next = update(values.bindings)
    setOrigins((prev) => originsAfterChange(prev, values.bindings, next, touched))
    change((v) => ({ ...v, bindings: next }))
  }
  function updateExposedRows(update: (prev: ExposedParamRow[]) => ExposedParamRow[]) {
    change((v) => ({ ...v, exposedRows: update(v.exposedRows) }))
  }

  function toggleRow(field: FieldKey) {
    const opening = !expanded.has(field)
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(field)) next.delete(field)
      else next.add(field)
      return next
    })
    // 開いた項目の道筋を図で強調する。閉じたら、ほかに開いている項目があればそれに移す。
    if (opening) setMapFocus(field)
    else if (mapFocus === field) setMapFocus(fields.find((f) => f !== field && expanded.has(f)) ?? null)
  }

  /** カードの行の「図で選ぶ」。選べるノードを図で示す(図は上に留まっているので、ページは動かさない)。 */
  function startPickInMap(field: FieldKey) {
    setPickField(field)
    setMapFocus(field)
    setMapSelected(null)
  }

  function changeBindingView(next: BindingView) {
    setBindingView(next)
    storeBindingView(next)
    setPickField(null)
  }

  /** 図でノードを選んだ。右のパネルを「ノード」にして、その位置までページを動かす。 */
  function selectMapNode(id: string | null) {
    setMapSelected(id)
    if (id === null) return
    setPanelTab('node')
    window.requestAnimationFrame(() => {
      const el = document.getElementById('binding-map-panel')
      if (!el) return
      const top = el.getBoundingClientRect().top
      // パネルの先頭が画面の上(ヘッダーの下)より上にあるか、画面の外にあるときだけ動かす。
      if (top < 80 || top > window.innerHeight - 120) el.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    })
  }

  /** 図の入力の一覧で、入力の扱い(差し込む・公開・固定)を選んだ。テンプレートの値は変えない。 */
  function chooseInputFromMap(nodeId: string, inputName: string, choice: InputChoice) {
    const node = nodes.find((n) => n.id === nodeId)
    const input = node?.inputs?.find((i) => i.name === inputName)
    if (!node || !input || input.linked) return
    const ref = { node: nodeId, input: inputName }
    if (choice.kind === 'bind') {
      changeBindings((b) => assignInputRole(b, choice.field, ref), [choice.field])
      updateExposedRows((rows) => setInputExposed(rows, node, input, false))
      setMapFocus(choice.field)
      if (pickField !== null) {
        setPickField(null)
        setFlashField(choice.field)
        setPanelTab('bindings')
      }
      return
    }
    changeBindings((b) => withoutInputRef(b, ref))
    updateExposedRows((rows) => setInputExposed(rows, node, input, choice.kind === 'exposed'))
  }

  function toggleOutputFromMap(nodeId: string) {
    changeBindings((b) => toggleOutput(b, nodeId), ['outputs'])
    setMapFocus('outputs')
    if (pickField === 'outputs') {
      setPickField(null)
      setFlashField('outputs')
      setPanelTab('bindings')
    }
  }

  function toggleFinalPromptFromMap(nodeId: string) {
    changeBindings((b) => toggleFinalPrompt(b, nodeId), ['final_prompt'])
    setMapFocus('final_prompt')
    if (pickField === 'final_prompt') {
      setPickField(null)
      setFlashField('final_prompt')
      setPanelTab('bindings')
    }
  }

  function focusElement(id: string) {
    // 描画のあとに移る(開いたばかりの行も含めて)。
    window.requestAnimationFrame(() => {
      const el = document.getElementById(id)
      if (!el) return
      el.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
      el.focus({ preventScroll: true })
    })
  }

  function jumpToField(field: FieldKey) {
    setFlashField(field)
    focusElement(bindingRowId(field))
  }

  function handleJump(target: IssueTarget) {
    if (target.kind === 'field') jumpToField(target.field)
    else if (target.kind === 'exposed') focusElement('exposed-params')
  }

  function answerQuestion(question: PendingQuestion, candidate: ComfyBindingCandidate | null) {
    changeBindings((b) => applyAnswer(b, question, candidate, nodes), question.fields)
  }

  function neitherQuestion(question: PendingQuestion) {
    setExpanded((prev) => new Set([...prev, ...question.fields]))
    jumpToField(question.fields[0])
  }

  function adoptReading(field: FieldKey) {
    if (!analysis) return
    const next = withReadingValue(values.bindings, analysis.suggested_bindings, field)
    setOrigins((prev) => ({ ...prev, [field]: 'reading' }))
    change((v) => ({ ...v, bindings: next }))
  }

  /** アシスタントの提案を1項目採る。出どころは「手で選択」にし、同じ検証に掛かる。 */
  function adoptSuggestedField(response: ComfySuggestResponse, field: FieldKey) {
    changeBindings((b) => adoptSuggestion(b, response, field), [field])
    setFlashField(field)
  }

  function tryInStudio() {
    if (!workflowId) return
    // 生成画面のフォームで、このワークフローを選んだ状態にする(ふつうの Run として試す)。
    const state = { ...formState, provider: 'comfyui', model: workflowId, params: {} }
    setFormState(state)
    requestFormLoad({ kind: 'run', state })
    void navigate('/studio')
  }

  function handleFile(file: File) {
    setFileError(null)
    setNotice(null)
    const reader = new FileReader()
    reader.onload = () => {
      let parsed: unknown
      try {
        parsed = JSON.parse(String(reader.result))
      } catch {
        setFileError(f.jsonParseFailed)
        return
      }
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setFileError(f.jsonParseFailed)
        return
      }
      const templateObj = parsed as Record<string, unknown>
      const templateText = String(reader.result)

      analyzeMutation.mutate(templateText, {
        onSuccess: (result) => {
          const first = values.template === null
          const applied = applyAnalyzedTemplate(values, templateObj, result)
          let next: WorkflowFormValues = { ...applied.values, templateText }
          if (first) {
            // 新規登録: 推奨の段の公開パラメーターは最初から公開する(ADR-0052 2章)。
            next = { ...next, exposedRows: enableRecommendedRows(next.exposedRows, result.param_tiers) }
            setOrigins(initialOrigins('reading'))
            const nextReadings = readingsByField(result.readings)
            const opened = FIELD_ORDER.filter((field) => initiallyExpanded(nextReadings[field], 'reading'))
            setExpanded(new Set(opened))
            setMapFocus(opened[0] ?? null)
          } else {
            setOrigins((prev) =>
              originsAfterReplace(prev, next.bindings, result.suggested_bindings, isEdit ? 'saved' : 'manual'),
            )
          }
          change(() => next)
          setAnalysis(result)
          setFileName(file.name)
          setIssues(result.validation ?? [])
          // 読み取り(カード)で示す警告は重ねて出さない。
          setWarnings(generalWarningMessages(result.warning_items))
          if (applied.cleared.length > 0) {
            setNotice(fmt(f.clearedFieldsNotice, { cleared: applied.cleared.join(f.listSeparator) }))
          }
        },
        onError: (err: unknown) => {
          setFileError(err instanceof ApiError ? err.message : f.analyzeFailed)
        },
      })
    }
    reader.onerror = () => setFileError(f.fileReadFailed)
    reader.readAsText(file)
  }

  function handleFileInputChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
    e.target.value = ''
  }

  function handleDrop(e: ReactDragEvent<HTMLDivElement>) {
    e.preventDefault()
    const file = e.dataTransfer.files?.[0]
    if (file) handleFile(file)
  }

  function handleDragOver(e: ReactDragEvent<HTMLDivElement>) {
    e.preventDefault()
  }

  const loadFailed = isEdit && workflowQuery.isError
  const loading = isEdit && !loadFailed && saved === null
  const changedAttr = (key: WorkflowFormDraftKey) => (changedKeys.includes(key) ? 'true' : undefined)
  const summary = summarizeRows(fields, readings, origins)
  const differCount =
    isEdit && analysis
      ? fields.filter(
          (field) =>
            origins[field] === 'saved' &&
            savedComparison(bindings, analysis.suggested_bindings, readings[field], field) === 'differ',
        ).length
      : null
  const headerStatus =
    template !== null && blockerCount > 0 ? fmt(c.headerBlocked, { count: blockerCount }) : undefined

  // 図はノードがあるときだけ選べる(読み取りに失敗した編集画面でも、ノードの入力の配線から描く)。
  const canShowMap = template !== null && nodes.length > 0
  const showMap = canShowMap && bindingView === 'map'

  const renderRows = (onPickInMap?: (field: FieldKey) => void) => (
    <BindingRows
      fields={fields}
      operation={operation}
      readings={readings}
      origins={origins}
      bindings={bindings}
      nodes={nodes}
      suggested={analysis?.suggested_bindings ?? null}
      issues={issues}
      questions={questions}
      expanded={expanded}
      onToggle={toggleRow}
      onChange={(field, update) => changeBindings(update, [field])}
      onUseReading={adoptReading}
      flashField={flashField}
      onGoToQuestion={(id) => focusElement(questionCardId(id))}
      onPickInMap={onPickInMap}
    />
  )

  const mapWorkspace = showMap ? (
    <BindingMap
      nodes={nodes}
      edges={diagramEdges}
      bindings={bindings}
      exposedRows={effectiveRows}
      readings={analysis?.readings ?? NO_READINGS}
      focusField={mapFocus}
      pickField={isMobile ? null : pickField}
      onCancelPick={() => setPickField(null)}
      selected={mapSelected}
      onSelect={selectMapNode}
      readOnly={isMobile}
      onChooseInput={chooseInputFromMap}
      onToggleOutput={toggleOutputFromMap}
      onToggleFinalPrompt={toggleFinalPromptFromMap}
      renderPanel={(graph) => (
        <>
          <div className={styles.panelTabs} role="tablist" aria-label={t.comfyui.map.panelLabel}>
            {(['bindings', 'node'] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={panelTab === tab}
                className={styles.panelTab}
                onClick={() => setPanelTab(tab)}
              >
                {tab === 'bindings' ? t.comfyui.map.tabBindings : t.comfyui.map.tabNode}
              </button>
            ))}
          </div>
          {panelTab === 'bindings' ? (
            renderRows(isMobile ? undefined : startPickInMap)
          ) : (
            <BindingMapInspector
              selected={mapSelected}
              graph={graph}
              nodes={nodes}
              edges={diagramEdges}
              bindings={bindings}
              exposedRows={effectiveRows}
              operation={operation}
              pickField={isMobile ? null : pickField}
              readOnly={isMobile}
              onSelect={selectMapNode}
              onChooseInput={chooseInputFromMap}
              onToggleOutput={toggleOutputFromMap}
              onToggleFinalPrompt={toggleFinalPromptFromMap}
            />
          )}
        </>
      )}
    />
  ) : null

  return (
    <SettingsPageFrame
      pageId="comfyui"
      title={isEdit ? f.editTitle : f.newTitle}
      draft={loading || loadFailed ? undefined : draft}
      status={headerStatus}
      onBack={() => goToComfyUIPage(navigate, location)}
      wide={showMap}
    >
      {loading && <p className={settingsStyles.placeholder}>{f.loading}</p>}
      {loadFailed && <p className={settingsStyles.errorText}>{f.workflowLoadError}</p>}

      {!loading && !loadFailed && (
        <>
          {showSavedCard && isEdit && !dirty && (
            <WorkflowSavedCard
              name={values.name}
              operationLabel={t.comfyui.operationLabels[operation]}
              bindingCount={fields.filter((field) => fieldIsSet(bindings, field)).length}
              exposedCount={effectiveRows.filter((row) => row.enabled).length}
              warningCount={serverWarnings.length}
              onTry={tryInStudio}
              onBackToList={() => goToComfyUIPage(navigate, location)}
              onClose={() => setShowSavedCard(false)}
            />
          )}

          {disconnectedNotice && (
            <div className={settingsStyles.warningBox}>
              <p className={settingsStyles.warningText}>
                {disconnectedNotice} <Link to={COMFYUI_SETTINGS_PATH}>{f.connectionPageLink}</Link>
              </p>
            </div>
          )}

          <SettingsSection heading={f.fileSectionHeading}>
            <div
              className={template === null ? styles.dropZone : styles.fileRow}
              data-changed={changedAttr('template')}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
            >
              {template === null ? (
                <p className={styles.dropZoneText}>{f.dropZoneText}</p>
              ) : (
                <span className={styles.fileChip}>
                  <span className={styles.fileName}>{fileName ?? c.savedTemplate}</span>
                  <span className={styles.fileMeta}>{fmt(c.nodeCount, { count: nodes.length })}</span>
                </span>
              )}
              <button
                type="button"
                className={settingsStyles.secondaryButton}
                disabled={analyzeMutation.isPending}
                onClick={() => fileInputRef.current?.click()}
              >
                {analyzeMutation.isPending ? f.analyzing : template === null ? f.chooseFile : f.replaceFile}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/json,.json"
                className={styles.hiddenFileInput}
                onChange={handleFileInputChange}
              />
            </div>
            {fileError && <p className={settingsStyles.errorText}>{fileError}</p>}
            {warnings.length > 0 && (
              <ul className={styles.warningList}>
                {warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {notice && <p className={settingsStyles.helpText}>{notice}</p>}
          </SettingsSection>

          {template !== null && (
            <>
              <SettingsSection heading={f.basicInfoHeading}>
                <div className={styles.basicGrid}>
                  <div className={styles.field} data-changed={changedAttr('name')}>
                    <label htmlFor="wf-name" className={styles.fieldLabel}>
                      {f.nameLabel}
                    </label>
                    <input
                      id="wf-name"
                      className={`${settingsStyles.input} ${styles.fullWidth}`}
                      value={name}
                      onChange={(e) => {
                        const next = e.target.value
                        change((v) => ({ ...v, name: next }))
                      }}
                    />
                  </div>
                  <div className={styles.field} data-changed={changedAttr('operation')}>
                    <label htmlFor="wf-operation" className={styles.fieldLabel} title={f.operationTitle}>
                      {f.operationLabel}
                    </label>
                    <select
                      id="wf-operation"
                      className={`${settingsStyles.select} ${styles.fullWidth}`}
                      value={operation}
                      onChange={(e) => {
                        const next = e.target.value as ComfyOperation
                        change((v) => withOperation(v, next))
                      }}
                    >
                      {operationOptions(t).map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className={`${styles.field} ${styles.gridFullRow}`} data-changed={changedAttr('locality')}>
                    <label htmlFor="wf-locality" className={styles.fieldLabel}>
                      {t.settings.locality.label}
                    </label>
                    <WorkflowLocalitySelect
                      id="wf-locality"
                      className={`${settingsStyles.select} ${styles.fullWidth}`}
                      value={values.locality}
                      onChange={(next) => change((v) => ({ ...v, locality: next }))}
                    />
                  </div>
                </div>
              </SettingsSection>

              {/*
                差し込み先(ADR-0052 4章)。読み取りの要約・問い・検証の一覧は共通。その下は「一覧(カード)」と
                「図」のどちらか一方を出す(状態は共有する)。図の表示は左に図、右にパネル。スクロールはページだけ。
              */}
              <SettingsSection heading={c.readingHeading}>
                <p className={settingsStyles.helpText}>{c.readingLead}</p>
                <ReadingSummary summary={summary} differCount={differCount} noReading={analysis === null} />
                {canShowMap && (
                  <div className={styles.viewSwitch} role="group" aria-label={t.comfyui.map.viewSwitchLabel}>
                    {(['list', 'map'] as const).map((view) => (
                      <button
                        key={view}
                        type="button"
                        aria-pressed={bindingView === view}
                        onClick={() => changeBindingView(view)}
                      >
                        {view === 'list' ? t.comfyui.map.viewList : t.comfyui.map.viewMap}
                      </button>
                    ))}
                  </div>
                )}
                {canSuggest && (
                  <AssistantSuggestPanel
                    workflowId={isEdit && !values.templateText ? (workflowId ?? null) : null}
                    templateText={values.templateText ?? JSON.stringify(template)}
                    template={template}
                    name={name}
                    operation={operation}
                    bindings={bindings}
                    nodes={nodes}
                    onAdopt={adoptSuggestedField}
                    onAdoptOperation={(next) => change((v) => withOperation(v, next))}
                  />
                )}
              </SettingsSection>

              {questions.map((question) => (
                <BindingQuestionCard
                  key={question.id}
                  question={question}
                  nodes={nodes}
                  onAnswer={(candidate) => answerQuestion(question, candidate)}
                  onNeither={() => neitherQuestion(question)}
                />
              ))}

              <BindingIssues
                issues={issues}
                clientErrors={dirty ? clientErrorsShown : []}
                onJump={handleJump}
                confirming={confirming}
                saving={saveMutation.isPending}
                onConfirmSave={saveNow}
                onCancelConfirm={() => setConfirming(false)}
              />

              {showMap ? (
                mapWorkspace
              ) : (
                <>
                  <SettingsSection heading={f.bindingsHeading}>
                    <p className={settingsStyles.helpText}>{c.bindingsLead}</p>
                    {renderRows()}
                  </SettingsSection>

                  <SettingsSection heading={f.exposedParamsHeading} id="exposed-params">
                    <p className={settingsStyles.helpText}>{c.exposedLead}</p>
                    <ExposedParamsTiers
                      rows={exposedRows}
                      tiers={analysis?.param_tiers}
                      bindings={bindings}
                      nodes={nodes}
                      onChange={(index, row) =>
                        updateExposedRows((rows) => rows.map((r, i) => (i === index ? row : r)))
                      }
                    />
                  </SettingsSection>

                  <details className={styles.advanced}>
                    <summary className={styles.advancedSummary}>
                      <span className={styles.advancedTitle}>{c.advancedTitle}</span>
                      <span className={styles.advancedSub}>{c.advancedSub}</span>
                    </summary>
                    <AdvancedBindings
                      operation={operation}
                      nodes={nodes}
                      bindings={bindings}
                      onChange={(update) => changeBindings(update)}
                    />
                  </details>
                </>
              )}
            </>
          )}
        </>
      )}
    </SettingsPageFrame>
  )
}

/** 詳細設定: 今までの2段のドロップダウン(ノード ID と入力名で直接選ぶ)。値は上の行と共有する。 */
function AdvancedBindings({
  operation,
  nodes,
  bindings,
  onChange,
}: {
  operation: ComfyOperation
  nodes: WorkflowFormValues['nodes']
  bindings: BindingsFormState
  onChange: (update: (prev: BindingsFormState) => BindingsFormState) => void
}) {
  const { t } = useI18n()
  const f = t.comfyui.form
  return (
    <div className={styles.advancedBody}>
      <div className={styles.bindingsGrid}>
        <NodeInputPicker
          label={f.promptLabel}
          nodes={nodes}
          value={bindings.prompt}
          onChange={(ref) => onChange((b) => ({ ...b, prompt: ref }))}
        />
        <NodeInputPicker
          label={f.negativePromptLabel}
          nodes={nodes}
          value={bindings.negativePrompt}
          onChange={(ref) => onChange((b) => ({ ...b, negativePrompt: ref }))}
        />
        <NodeInputPicker
          label={f.widthLabel}
          nodes={nodes}
          value={bindings.width}
          onChange={(ref) => onChange((b) => ({ ...b, width: ref }))}
        />
        <NodeInputPicker
          label={f.heightLabel}
          nodes={nodes}
          value={bindings.height}
          onChange={(ref) => onChange((b) => ({ ...b, height: ref }))}
        />
        <NodeInputPicker
          label={f.batchSizeLabel}
          nodes={nodes}
          value={bindings.batchSize}
          onChange={(ref) => onChange((b) => ({ ...b, batchSize: ref }))}
        />
      </div>

      {operation === 'edit' && (
        <div className={styles.subField}>
          <span className={styles.fieldLabel}>{f.imageSlotsLabel}</span>
          <p className={settingsStyles.helpText}>{f.imageSlotsHelp}</p>
          <div className={styles.seedList}>
            {bindings.images.map((ref, index) => (
              <div key={index} className={styles.seedRow}>
                <NodeInputPicker
                  label={fmt(f.imageSlotLabel, { index: index + 1 })}
                  nodes={nodes}
                  value={ref.node ? ref : null}
                  onChange={(next) =>
                    onChange((b) => {
                      const images = [...b.images]
                      images[index] = next ?? { node: '', input: '' }
                      return { ...b, images }
                    })
                  }
                />
                <div className={styles.reorderButtons}>
                  <button
                    type="button"
                    aria-label={f.moveUp}
                    disabled={index === 0}
                    onClick={() => onChange((b) => ({ ...b, images: moveImageSlot(b.images, index, 'up') }))}
                  >
                    ▲
                  </button>
                  <button
                    type="button"
                    aria-label={f.moveDown}
                    disabled={index === bindings.images.length - 1}
                    onClick={() => onChange((b) => ({ ...b, images: moveImageSlot(b.images, index, 'down') }))}
                  >
                    ▼
                  </button>
                </div>
                <button
                  type="button"
                  className={settingsStyles.dangerButton}
                  onClick={() => onChange((b) => ({ ...b, images: b.images.filter((_, i) => i !== index) }))}
                >
                  {f.remove}
                </button>
              </div>
            ))}
            <button
              type="button"
              className={styles.addButton}
              onClick={() => onChange((b) => ({ ...b, images: [...b.images, { node: '', input: '' }] }))}
            >
              {f.addImageSlot}
            </button>
          </div>
        </div>
      )}

      <div className={styles.subField}>
        <span className={styles.fieldLabel}>{f.seedsLabel}</span>
        <div className={styles.seedList}>
          {bindings.seeds.map((seed, index) => (
            <div key={index} className={styles.seedRow}>
              <NodeInputPicker
                label={fmt(f.seedLabel, { index: index + 1 })}
                nodes={nodes}
                value={seed.node ? seed : null}
                onChange={(ref) =>
                  onChange((b) => {
                    const seeds = [...b.seeds]
                    seeds[index] = ref ?? { node: '', input: '' }
                    return { ...b, seeds }
                  })
                }
              />
              <button
                type="button"
                className={settingsStyles.dangerButton}
                onClick={() => onChange((b) => ({ ...b, seeds: b.seeds.filter((_, i) => i !== index) }))}
              >
                {f.remove}
              </button>
            </div>
          ))}
          <button
            type="button"
            className={styles.addButton}
            onClick={() => onChange((b) => ({ ...b, seeds: [...b.seeds, { node: '', input: '' }] }))}
          >
            {f.addSeed}
          </button>
        </div>
      </div>

      {operation === 'edit' && (
        <div className={styles.subField}>
          <span className={styles.fieldLabel}>{f.maskLabel}</span>
          <div className={styles.maskModeRow}>
            <label>
              <input
                type="radio"
                name="mask-mode"
                checked={bindings.maskMode === 'none'}
                onChange={() => onChange((b) => ({ ...b, maskMode: 'none', maskRef: null }))}
              />
              {f.maskModeNone}
            </label>
            <label>
              <input
                type="radio"
                name="mask-mode"
                checked={bindings.maskMode === 'load_image_mask'}
                onChange={() => onChange((b) => ({ ...b, maskMode: 'load_image_mask' }))}
              />
              {f.maskModeLoadImageMask}
            </label>
            <label>
              <input
                type="radio"
                name="mask-mode"
                checked={bindings.maskMode === 'image_alpha'}
                onChange={() => onChange((b) => ({ ...b, maskMode: 'image_alpha', maskRef: null }))}
              />
              {f.maskModeImageAlpha}
            </label>
          </div>
          {bindings.maskMode === 'load_image_mask' && (
            <NodeInputPicker
              label={f.maskRefLabel}
              nodes={nodes}
              value={bindings.maskRef}
              onChange={(ref) => onChange((b) => ({ ...b, maskRef: ref }))}
            />
          )}
        </div>
      )}

      <div className={styles.subField}>
        <NodeMultiPicker
          label={f.outputsLabel}
          nodes={nodes}
          value={bindings.outputs}
          onChange={(outputs) => onChange((b) => ({ ...b, outputs }))}
        />
      </div>

      <div className={styles.subField}>
        <NodeSelectPicker
          label={f.finalPromptLabel}
          nodes={nodes}
          value={bindings.finalPrompt}
          onChange={(finalPrompt) => onChange((b) => ({ ...b, finalPrompt }))}
        />
        <p className={settingsStyles.helpText}>{f.finalPromptHelp}</p>
      </div>
    </div>
  )
}

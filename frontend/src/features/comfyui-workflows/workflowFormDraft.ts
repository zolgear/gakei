/**
 * ワークフローの登録・編集画面(`/settings/comfyui/workflows/new`、`/:id`)を、設定の枠の中の
 * 「保存で反映」のページとして扱うための純粋関数(ADR-0031 1章の 2026-10-01 追記、3・4章)。
 * - フォームの値は1つのオブジェクト(`WorkflowFormValues`)で持ち、保存済みの値と比べて
 *   変えた欄(ヘッダーの変更件数)を出す。`nodes` はテンプレートから決まる補助の値なので数えない。
 * - analyze の結果をフォームに当てはめる(初回の読み込みと、ファイルの差し替え)。
 * - 「戻る」と保存後の行き先は ComfyUI のページ。そこから開いたなら1つ戻る。
 */
import type { ComfyAnalyzeResponse, ComfyNodeInfo, ComfyOperation, ComfyWorkflowDetail } from '../../api/client'
import { isSameDraftValue } from '../settings/settingsDraft'
import {
  bindingsFormFromApi,
  bindingsFormFromSuggestion,
  EMPTY_BINDINGS_FORM,
  reconcileBindingsWithNodes,
  type BindingsFormState,
} from './bindingsForm'
import { buildExposedParamRows, reconcileExposedParamsOnReplace, type ExposedParamRow } from './exposedParamsForm'

export interface WorkflowFormValues {
  name: string
  operation: ComfyOperation
  template: Record<string, unknown> | null
  /** テンプレートの analyze で得たノード(選択肢)。変更の件数には数えない。 */
  nodes: ComfyNodeInfo[]
  bindings: BindingsFormState
  exposedRows: ExposedParamRow[]
}

/** 変更の件数に数える欄(画面に出る順)。 */
export const WORKFLOW_FORM_DRAFT_KEYS = ['name', 'operation', 'template', 'bindings', 'exposedRows'] as const

export type WorkflowFormDraftKey = (typeof WORKFLOW_FORM_DRAFT_KEYS)[number]

/** 新規登録の初期値(保存済みの値として扱う)。 */
export const EMPTY_WORKFLOW_FORM: WorkflowFormValues = {
  name: '',
  operation: 'generate',
  template: null,
  nodes: [],
  bindings: EMPTY_BINDINGS_FORM,
  exposedRows: [],
}

/** 保存済みの値と違う欄。テンプレートは同じオブジェクトなら中身を比べない(大きいことがある)。 */
export function changedWorkflowFormKeys(saved: WorkflowFormValues, values: WorkflowFormValues): WorkflowFormDraftKey[] {
  return WORKFLOW_FORM_DRAFT_KEYS.filter((key) => !isSameDraftValue(saved[key], values[key]))
}

/**
 * 編集画面の初期値。保存済みの bindings / exposed_params を使い、analyze の提案では上書きしない。
 * analyze に失敗したとき(`analyzed` が null)も、保存済みの値はそのまま残す(保存で消さないため)。
 */
export function workflowFormFromSaved(
  wf: Pick<ComfyWorkflowDetail, 'name' | 'operation' | 'template' | 'bindings' | 'exposed_params'>,
  analyzed: Pick<ComfyAnalyzeResponse, 'nodes' | 'candidate_params'> | null,
): WorkflowFormValues {
  return {
    name: wf.name,
    operation: wf.operation,
    template: wf.template,
    nodes: analyzed?.nodes ?? [],
    bindings: bindingsFormFromApi(wf.bindings),
    exposedRows: buildExposedParamRows(analyzed?.candidate_params ?? [], wf.exposed_params ?? []),
  }
}

export interface AppliedTemplate {
  values: WorkflowFormValues
  /** 新しいファイルに無いため未設定に戻した差し込み先・公開パラメーターの名前(通知に使う)。 */
  cleared: string[]
}

/**
 * 選んだファイルの analyze の結果をフォームに当てはめる。
 * - 初めてのファイル: 提案(差し込み先・操作・公開パラメーターの候補)を初期値にする。名前はそのまま。
 * - 差し替え: 今の差し込み先・公開パラメーターのうち、新しいノードに無いものだけ未設定に戻す。
 */
export function applyAnalyzedTemplate(
  prev: WorkflowFormValues,
  template: Record<string, unknown>,
  result: Pick<ComfyAnalyzeResponse, 'nodes' | 'candidate_params' | 'suggested_bindings' | 'suggested_operation'>,
): AppliedTemplate {
  const nodes = result.nodes ?? []
  const candidates = result.candidate_params ?? []
  if (prev.template === null) {
    return {
      values: {
        ...prev,
        template,
        nodes,
        operation: result.suggested_operation,
        bindings: bindingsFormFromSuggestion(result.suggested_bindings),
        exposedRows: buildExposedParamRows(candidates, []),
      },
      cleared: [],
    }
  }
  const { next: bindings, clearedFields } = reconcileBindingsWithNodes(prev.bindings, nodes)
  const { next: exposedRows, clearedNames } = reconcileExposedParamsOnReplace(prev.exposedRows, candidates, nodes)
  return {
    values: { ...prev, template, nodes, bindings, exposedRows },
    cleared: [...clearedFields, ...clearedNames],
  }
}

/** 操作を切り替える。generate は入力画像もマスクも取らない(ADR-0013)ので、値を落としておく。 */
export function withOperation(prev: WorkflowFormValues, operation: ComfyOperation): WorkflowFormValues {
  if (operation === 'generate') {
    return { ...prev, operation, bindings: { ...prev.bindings, images: [], maskMode: 'none', maskRef: null } }
  }
  return { ...prev, operation }
}

/** ComfyUI のページ(ワークフローの一覧がある)。 */
export const COMFYUI_SETTINGS_PATH = '/settings/comfyui'

/** 登録(`id` なし)・編集の画面のパス。 */
export function workflowFormPath(id?: string): string {
  return id === undefined ? '/settings/comfyui/workflows/new' : `/settings/comfyui/workflows/${id}`
}

/** ComfyUI のページから登録・編集を開くとき、履歴の state に付ける印。 */
export const FROM_COMFYUI_PAGE_STATE = { fromComfyUISettings: true } as const

export function isOpenedFromComfyUIPage(state: unknown): boolean {
  return typeof state === 'object' && state !== null && (state as Record<string, unknown>).fromComfyUISettings === true
}

export type WorkflowFormBackDestination = { type: 'back' } | { type: 'replace'; path: string }

/**
 * 登録・編集の画面の「戻る」と、保存に成功したあとの行き先(どちらも ComfyUI のページ)。
 * ComfyUI のページから開いたなら1つ戻る(そのページの履歴の state を保つ。狭い幅で目次から
 * 開いた印など)。直接開いた・ほかの画面から来たときは ComfyUI のページへ置き換える。
 */
export function workflowFormBackDestination(params: {
  openedFromComfyUIPage: boolean
  /** react-router の `location.key`。直接開いたときは `'default'`。 */
  locationKey: string
}): WorkflowFormBackDestination {
  return params.openedFromComfyUIPage && params.locationKey !== 'default'
    ? { type: 'back' }
    : { type: 'replace', path: COMFYUI_SETTINGS_PATH }
}

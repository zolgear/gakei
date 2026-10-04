/**
 * `/settings/comfyui/workflows/new` と `/settings/comfyui/workflows/:id`(ADR-0013、ADR-0031)。
 * 設定の枠(目次とヘッダー)の中で開き、目次は ComfyUI を選んだ状態にする(`pages/SettingsPage.tsx`)。
 * ComfyUI の「Export (API)」で書き出した JSON を選び、analyze の提案を初期値にして差し込み先・
 * 公開パラメーターを整える。編集時は保存済みの bindings/exposed_params を初期値にする(analyze の
 * 提案では上書きしない)。判定・変換のロジックは同じディレクトリの純粋関数(*.ts)に委ねている。
 *
 * 「保存で反映」のページとして扱う(ADR-0031 2〜4章)。
 * - 保存はヘッダーの1か所。フォームの値を保存済みの値と比べ、変更の件数・「変更を取り消す」を出す。
 *   ヘッダーには `SettingsDraftControls` を満たす値を渡す(値の形が `useSettingsDraft` の
 *   キーごとの上書きに合わないため、ここで組み立てる)。保存は常に全体を送る(サーバーが全体を検証する)。
 * - 保存していない変更があれば、離れる前に確かめる(`SettingsPageFrame` の `UnsavedChangesGuard`)。
 * - 「戻る」と保存に成功したあとの行き先は ComfyUI のページ。保存後は、保存済みの値を更新して
 *   変更が無くなってから移動する(移動の確認が出ないように)。
 * - ファイルの選択(analyze)は「操作」。何が起きるかを書いた名前にし、「保存」とは書かない。
 *
 * 未接続(`enabled === false`)でも登録・編集はできるが、analyze の `/object_info` 補完が
 * 効かず、保存してもモデルの選択肢には出ないので、その旨を警告として出す(ADR-0013 7章)。
 */
import { useEffect, useRef, useState, type ChangeEvent, type DragEvent as ReactDragEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, type Location, type NavigateFunction } from 'react-router'
import {
  ApiError,
  analyzeComfyWorkflow,
  createComfyWorkflow,
  getComfyUIStatus,
  getComfyWorkflow,
  updateComfyWorkflow,
  type ComfyOperation,
} from '../../api/client'
import { fmt, useI18n, type Messages } from '../../i18n'
import { canSaveDraft } from '../settings/settingsDraft'
import { SettingsPageFrame } from '../settings/SettingsPageFrame'
import { SettingsSection } from '../settings/SettingsParts'
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
  type WorkflowFormDraftKey,
  type WorkflowFormValues,
} from './workflowFormDraft'
import { NodeInputPicker } from './NodeInputPicker'
import { NodeMultiPicker } from './NodeMultiPicker'
import { NodeSelectPicker } from './NodeSelectPicker'
import { ExposedParamsTable } from './ExposedParamsTable'
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

interface ComfyUIWorkflowFormPageProps {
  /** 編集するワークフロー。省くと新規登録。 */
  workflowId?: string
}

export function ComfyUIWorkflowFormPage({ workflowId }: ComfyUIWorkflowFormPageProps) {
  const { t } = useI18n()
  const f = t.comfyui.form
  const isEdit = workflowId !== undefined
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useSettingsShell()
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const workflowQuery = useQuery({
    queryKey: ['comfyui-workflow', workflowId],
    queryFn: () => getComfyWorkflow(workflowId as string),
    enabled: isEdit,
  })

  const statusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })
  const disconnectedNotice = statusQuery.data ? formDisconnectedNotice(statusQuery.data.enabled) : null

  // 保存済みの値(編集画面では読み込みと analyze が済むまで null)と、画面の値。
  const [saved, setSaved] = useState<WorkflowFormValues | null>(isEdit ? null : EMPTY_WORKFLOW_FORM)
  const [values, setValues] = useState<WorkflowFormValues>(EMPTY_WORKFLOW_FORM)
  const [warnings, setWarnings] = useState<string[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  // 保存に成功したら ComfyUI のページへ戻る(変更が無くなったのを見届けてから)。
  const [leaveAfterSave, setLeaveAfterSave] = useState(false)
  const leftRef = useRef(false)

  // 編集画面: 保存済みのワークフローを読み込んだら、そのテンプレートで analyze を呼び直して
  // ノードの選択肢を作る。フォームの初期値には保存済みの bindings/exposed_params を使う。
  // analyze に失敗しても保存済みの値は残す(選択肢が無いだけ)。
  useEffect(() => {
    if (!isEdit || !workflowQuery.data || saved !== null) return
    const wf = workflowQuery.data
    let cancelled = false
    analyzeComfyWorkflow(wf.template)
      .then((result) => {
        if (cancelled) return
        const initial = workflowFormFromSaved(wf, result)
        setSaved(initial)
        setValues(initial)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const initial = workflowFormFromSaved(wf, null)
        setSaved(initial)
        setValues(initial)
        setFileError(err instanceof ApiError ? err.message : f.templateParseFailed)
      })
    return () => {
      cancelled = true
    }
  }, [isEdit, workflowQuery.data, saved, f.templateParseFailed])

  const analyzeMutation = useMutation({ mutationFn: analyzeComfyWorkflow })

  const saveMutation = useMutation({
    mutationFn: (input: WorkflowFormValues) => {
      if (input.template === null) throw new Error('template is not set')
      const args = { ...input, template: input.template }
      return isEdit
        ? updateComfyWorkflow(workflowId as string, buildWorkflowUpdateRequest(args))
        : createComfyWorkflow(buildWorkflowCreateRequest(args))
    },
    onSuccess: (_result, input) => {
      queryClient.invalidateQueries({ queryKey: ['capabilities'] })
      queryClient.invalidateQueries({ queryKey: ['comfyui-workflows'] })
      if (isEdit) queryClient.invalidateQueries({ queryKey: ['comfyui-workflow', workflowId] })
      toast.show({ message: t.settings.frame.savedToast })
      setSaved(input)
      setLeaveAfterSave(true)
    },
  })
  const resetSaveError = saveMutation.reset

  const changedKeys = saved ? changedWorkflowFormKeys(saved, values) : []
  const dirty = changedKeys.length > 0
  const clientErrors = validateWorkflowForm({
    name: values.name,
    operation: values.operation,
    hasTemplate: values.template !== null,
    bindings: values.bindings,
    exposedRows: values.exposedRows,
  })
  const canSave = canSaveDraft({
    changedCount: changedKeys.length,
    valid: clientErrors.length === 0,
    saving: saveMutation.isPending,
  })

  // 保存した値が保存済みになり、離れる前の確認が要らなくなってから移動する
  // (子の `UnsavedChangesGuard` の effect が先に走り、移動を止める条件が更新済みになる)。
  useEffect(() => {
    if (!leaveAfterSave || dirty || leftRef.current) return
    leftRef.current = true
    goToComfyUIPage(navigate, location)
  }, [leaveAfterSave, dirty, navigate, location])

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
      if (canSave) saveMutation.mutate(values)
    },
    reset: () => {
      if (saved) setValues(saved)
      setWarnings([])
      setNotice(null)
      setFileError(null)
      resetSaveError()
    },
  }

  /** 値を変える。前回の保存の失敗表示は消す。 */
  function change(update: (prev: WorkflowFormValues) => WorkflowFormValues) {
    setValues(update)
    resetSaveError()
  }
  function updateBindings(update: (prev: BindingsFormState) => BindingsFormState) {
    change((v) => ({ ...v, bindings: update(v.bindings) }))
  }
  function updateExposedRows(update: (prev: ExposedParamRow[]) => ExposedParamRow[]) {
    change((v) => ({ ...v, exposedRows: update(v.exposedRows) }))
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

      analyzeMutation.mutate(templateObj, {
        onSuccess: (result) => {
          const applied = applyAnalyzedTemplate(values, templateObj, result)
          change(() => applied.values)
          setWarnings(result.warnings ?? [])
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
  const { name, operation, template, nodes, bindings, exposedRows } = values
  const changedAttr = (key: WorkflowFormDraftKey) => (changedKeys.includes(key) ? 'true' : undefined)

  return (
    <SettingsPageFrame
      pageId="comfyui"
      title={isEdit ? f.editTitle : f.newTitle}
      draft={loading || loadFailed ? undefined : draft}
      onBack={() => goToComfyUIPage(navigate, location)}
    >
      {loading && <p className={settingsStyles.placeholder}>{f.loading}</p>}
      {loadFailed && <p className={settingsStyles.errorText}>{f.workflowLoadError}</p>}

      {!loading && !loadFailed && (
        <>
          {disconnectedNotice && (
            <div className={settingsStyles.warningBox}>
              <p className={settingsStyles.warningText}>
                {disconnectedNotice} <Link to={COMFYUI_SETTINGS_PATH}>{f.connectionPageLink}</Link>
              </p>
            </div>
          )}

          <SettingsSection heading={f.fileSectionHeading}>
            <div
              className={styles.dropZone}
              data-changed={changedAttr('template')}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
            >
              <p className={styles.dropZoneText}>{f.dropZoneText}</p>
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
              {template !== null && (
                <p className={styles.templateStatus}>{fmt(f.templateLoaded, { nodeCount: nodes.length })}</p>
              )}
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
                </div>
              </SettingsSection>

              <SettingsSection heading={f.bindingsHeading}>
                <div className={styles.bindingsGrid}>
                  <NodeInputPicker
                    label={f.promptLabel}
                    nodes={nodes}
                    value={bindings.prompt}
                    onChange={(ref) => updateBindings((b) => ({ ...b, prompt: ref }))}
                  />
                  <NodeInputPicker
                    label={f.negativePromptLabel}
                    nodes={nodes}
                    value={bindings.negativePrompt}
                    onChange={(ref) => updateBindings((b) => ({ ...b, negativePrompt: ref }))}
                  />
                  <NodeInputPicker
                    label={f.widthLabel}
                    nodes={nodes}
                    value={bindings.width}
                    onChange={(ref) => updateBindings((b) => ({ ...b, width: ref }))}
                  />
                  <NodeInputPicker
                    label={f.heightLabel}
                    nodes={nodes}
                    value={bindings.height}
                    onChange={(ref) => updateBindings((b) => ({ ...b, height: ref }))}
                  />
                  <NodeInputPicker
                    label={f.batchSizeLabel}
                    nodes={nodes}
                    value={bindings.batchSize}
                    onChange={(ref) => updateBindings((b) => ({ ...b, batchSize: ref }))}
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
                              updateBindings((b) => {
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
                              onClick={() =>
                                updateBindings((b) => ({ ...b, images: moveImageSlot(b.images, index, 'up') }))
                              }
                            >
                              ▲
                            </button>
                            <button
                              type="button"
                              aria-label={f.moveDown}
                              disabled={index === bindings.images.length - 1}
                              onClick={() =>
                                updateBindings((b) => ({ ...b, images: moveImageSlot(b.images, index, 'down') }))
                              }
                            >
                              ▼
                            </button>
                          </div>
                          <button
                            type="button"
                            className={settingsStyles.dangerButton}
                            onClick={() =>
                              updateBindings((b) => ({ ...b, images: b.images.filter((_, i) => i !== index) }))
                            }
                          >
                            {f.remove}
                          </button>
                        </div>
                      ))}
                      <button
                        type="button"
                        className={styles.addButton}
                        onClick={() =>
                          updateBindings((b) => ({ ...b, images: [...b.images, { node: '', input: '' }] }))
                        }
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
                            updateBindings((b) => {
                              const seeds = [...b.seeds]
                              seeds[index] = ref ?? { node: '', input: '' }
                              return { ...b, seeds }
                            })
                          }
                        />
                        <button
                          type="button"
                          className={settingsStyles.dangerButton}
                          onClick={() =>
                            updateBindings((b) => ({ ...b, seeds: b.seeds.filter((_, i) => i !== index) }))
                          }
                        >
                          {f.remove}
                        </button>
                      </div>
                    ))}
                    <button
                      type="button"
                      className={styles.addButton}
                      onClick={() => updateBindings((b) => ({ ...b, seeds: [...b.seeds, { node: '', input: '' }] }))}
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
                          onChange={() => updateBindings((b) => ({ ...b, maskMode: 'none', maskRef: null }))}
                        />
                        {f.maskModeNone}
                      </label>
                      <label>
                        <input
                          type="radio"
                          name="mask-mode"
                          checked={bindings.maskMode === 'load_image_mask'}
                          onChange={() => updateBindings((b) => ({ ...b, maskMode: 'load_image_mask' }))}
                        />
                        {f.maskModeLoadImageMask}
                      </label>
                      <label>
                        <input
                          type="radio"
                          name="mask-mode"
                          checked={bindings.maskMode === 'image_alpha'}
                          onChange={() => updateBindings((b) => ({ ...b, maskMode: 'image_alpha', maskRef: null }))}
                        />
                        {f.maskModeImageAlpha}
                      </label>
                    </div>
                    {bindings.maskMode === 'load_image_mask' && (
                      <NodeInputPicker
                        label={f.maskRefLabel}
                        nodes={nodes}
                        value={bindings.maskRef}
                        onChange={(ref) => updateBindings((b) => ({ ...b, maskRef: ref }))}
                      />
                    )}
                  </div>
                )}

                <div className={styles.subField}>
                  <NodeMultiPicker
                    label={f.outputsLabel}
                    nodes={nodes}
                    value={bindings.outputs}
                    onChange={(outputs) => updateBindings((b) => ({ ...b, outputs }))}
                  />
                </div>

                <div className={styles.subField}>
                  <NodeSelectPicker
                    label={f.finalPromptLabel}
                    nodes={nodes}
                    value={bindings.finalPrompt}
                    onChange={(finalPrompt) => updateBindings((b) => ({ ...b, finalPrompt }))}
                  />
                  <p className={settingsStyles.helpText}>{f.finalPromptHelp}</p>
                </div>
              </SettingsSection>

              <SettingsSection heading={f.exposedParamsHeading}>
                <ExposedParamsTable
                  rows={exposedRows}
                  nodes={nodes}
                  onChange={(index, row) => updateExposedRows((rows) => rows.map((r, i) => (i === index ? row : r)))}
                />
              </SettingsSection>
            </>
          )}

          {/* ヘッダーの「保存」を押せない理由。変えたあとにだけ出す。 */}
          {dirty && clientErrors.length > 0 && (
            <div className={styles.validation} role="status">
              <p className={settingsStyles.helpText}>{f.cannotSaveYet}</p>
              <ul className={styles.errorList}>
                {clientErrors.map((err) => (
                  <li key={err}>{err}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </SettingsPageFrame>
  )
}

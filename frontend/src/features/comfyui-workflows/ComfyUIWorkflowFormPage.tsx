/**
 * `/settings/comfyui/new` と `/settings/comfyui/:id`(ADR-0013)。ComfyUI の「Export (API)」で
 * 書き出した JSON を選び、analyze の提案を初期値にして差し込み先・公開パラメーターを整え、
 * 保存する。編集時は保存済みの bindings/exposed_params を初期値にする(analyze の提案では
 * 上書きしない)。判定・変換のロジックは同じディレクトリの純粋関数(*.ts)に委ねている。
 * 未接続(`enabled === false`)でも登録・編集はできるが、analyze の `/object_info` 補完が
 * 効かず、保存してもモデルの選択肢には出ないので、その旨を警告として出す(ADR-0013 7章)。
 */
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent as ReactDragEvent,
} from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router'
import {
  ApiError,
  analyzeComfyWorkflow,
  createComfyWorkflow,
  getComfyUIStatus,
  getComfyWorkflow,
  updateComfyWorkflow,
  type ComfyNodeInfo,
  type ComfyOperation,
} from '../../api/client'
import { useBackNavigate } from '../../lib/useBackNavigate'
import { ToastHost, useToast } from '../../components/Toast'
import { fmt, msg, useI18n, type Messages } from '../../i18n'
import {
  bindingsFormFromApi,
  bindingsFormFromSuggestion,
  EMPTY_BINDINGS_FORM,
  moveImageSlot,
  reconcileBindingsWithNodes,
  type BindingsFormState,
} from './bindingsForm'
import {
  buildExposedParamRows,
  reconcileExposedParamsOnReplace,
  type ExposedParamRow,
} from './exposedParamsForm'
import { validateWorkflowForm } from './workflowFormValidation'
import { buildWorkflowCreateRequest, buildWorkflowUpdateRequest } from './workflowSave'
import { formDisconnectedNotice } from './comfyuiConnectionForm'
import { NodeInputPicker } from './NodeInputPicker'
import { NodeMultiPicker } from './NodeMultiPicker'
import { ExposedParamsTable } from './ExposedParamsTable'
import panelStyles from './ComfyUIStatusPanel.module.css'
import styles from './ComfyUIWorkflowFormPage.module.css'

function operationOptions(t: Messages): { value: ComfyOperation; label: string }[] {
  return [
    { value: 'generate', label: t.comfyui.form.generateOption },
    { value: 'edit', label: t.comfyui.form.editOption },
  ]
}

export function ComfyUIWorkflowFormPage() {
  const { t } = useI18n()
  const { id } = useParams<{ id: string }>()
  const isEdit = id !== undefined
  const navigate = useNavigate()
  const goBack = useBackNavigate('/settings/comfyui')
  const queryClient = useQueryClient()
  const toast = useToast()
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const workflowQuery = useQuery({
    queryKey: ['comfyui-workflow', id],
    queryFn: () => getComfyWorkflow(id as string),
    enabled: isEdit,
  })

  const statusQuery = useQuery({ queryKey: ['comfyui-status'], queryFn: getComfyUIStatus })
  const disconnectedNotice = statusQuery.data ? formDisconnectedNotice(statusQuery.data.enabled) : null

  const [name, setName] = useState('')
  const [operation, setOperation] = useState<ComfyOperation>('generate')
  const [template, setTemplate] = useState<Record<string, unknown> | null>(null)
  const [nodes, setNodes] = useState<ComfyNodeInfo[]>([])
  const [bindings, setBindings] = useState<BindingsFormState>(EMPTY_BINDINGS_FORM)
  const [exposedRows, setExposedRows] = useState<ExposedParamRow[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [initializedFromSaved, setInitializedFromSaved] = useState(false)

  // 編集画面: 保存済みのワークフローを読み込んだら、そのテンプレートで analyze を呼び直して
  // ノードの選択肢を作り、フォームの初期値には保存済みの bindings/exposed_params を使う
  // (analyze の suggested_bindings/candidate_params では上書きしない)。
  useEffect(() => {
    if (!isEdit || !workflowQuery.data || initializedFromSaved) return
    const wf = workflowQuery.data
    setName(wf.name)
    setOperation(wf.operation)
    setTemplate(wf.template)
    let cancelled = false
    analyzeComfyWorkflow(wf.template)
      .then((result) => {
        if (cancelled) return
        setNodes(result.nodes ?? [])
        setBindings(bindingsFormFromApi(wf.bindings))
        setExposedRows(buildExposedParamRows(result.candidate_params ?? [], wf.exposed_params ?? []))
        setInitializedFromSaved(true)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setFileError(err instanceof ApiError ? err.message : msg().comfyui.form.templateParseFailed)
      })
    return () => {
      cancelled = true
    }
  }, [isEdit, workflowQuery.data, initializedFromSaved])

  const analyzeMutation = useMutation({ mutationFn: analyzeComfyWorkflow })

  function handleFile(file: File) {
    setFileError(null)
    setNotice(null)
    const reader = new FileReader()
    reader.onload = () => {
      let parsed: unknown
      try {
        parsed = JSON.parse(String(reader.result))
      } catch {
        setFileError(t.comfyui.form.jsonParseFailed)
        return
      }
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setFileError(t.comfyui.form.jsonParseFailed)
        return
      }
      const templateObj = parsed as Record<string, unknown>
      const isReplace = template !== null

      analyzeMutation.mutate(templateObj, {
        onSuccess: (result) => {
          const newNodes = result.nodes ?? []
          setTemplate(templateObj)
          setNodes(newNodes)
          setWarnings(result.warnings ?? [])

          if (!isReplace) {
            setBindings(bindingsFormFromSuggestion(result.suggested_bindings))
            setOperation(result.suggested_operation)
            setExposedRows(buildExposedParamRows(result.candidate_params ?? [], []))
            return
          }

          const { next: nextBindings, clearedFields } = reconcileBindingsWithNodes(bindings, newNodes)
          const { next: nextRows, clearedNames } = reconcileExposedParamsOnReplace(
            exposedRows,
            result.candidate_params ?? [],
            newNodes,
          )
          setBindings(nextBindings)
          setExposedRows(nextRows)
          const cleared = [...clearedFields, ...clearedNames]
          if (cleared.length > 0) {
            setNotice(fmt(t.comfyui.form.clearedFieldsNotice, { cleared: cleared.join(t.comfyui.form.listSeparator) }))
          }
        },
        onError: (err: unknown) => {
          setFileError(err instanceof ApiError ? err.message : t.comfyui.form.analyzeFailed)
        },
      })
    }
    reader.onerror = () => setFileError(t.comfyui.form.fileReadFailed)
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

  function handleOperationChange(next: ComfyOperation) {
    setOperation(next)
    if (next === 'generate') {
      // generate は入力画像もマスクも取らない(ADR-0013)。切り替えたら値を落としておく。
      setBindings((b) => ({ ...b, images: [], maskMode: 'none', maskRef: null }))
    }
  }

  const clientErrors = validateWorkflowForm({
    name,
    operation,
    hasTemplate: template !== null,
    bindings,
    exposedRows,
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      if (template === null) throw new Error('template is not set')
      const args = { name, operation, template, bindings, exposedRows }
      return isEdit
        ? updateComfyWorkflow(id as string, buildWorkflowUpdateRequest(args))
        : createComfyWorkflow(buildWorkflowCreateRequest(args))
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['capabilities'] })
      queryClient.invalidateQueries({ queryKey: ['comfyui-workflows'] })
      if (isEdit) queryClient.invalidateQueries({ queryKey: ['comfyui-workflow', id] })
      toast.show({ message: isEdit ? t.comfyui.form.updated : t.comfyui.form.created })
      navigate('/settings/comfyui')
    },
  })

  const serverErrorMessage =
    saveMutation.error instanceof ApiError
      ? saveMutation.error.message
      : saveMutation.error
        ? t.comfyui.form.saveFailedGeneric
        : null

  const loading = isEdit && workflowQuery.isLoading

  if (loading) {
    return (
      <div className={styles.page}>
        <p className={styles.placeholder}>{t.comfyui.form.loading}</p>
      </div>
    )
  }

  if (isEdit && workflowQuery.isError) {
    return (
      <div className={styles.page}>
        <button type="button" className={styles.backLink} onClick={goBack}>
          {t.comfyui.form.back}
        </button>
        <p className={styles.placeholder}>{t.comfyui.form.workflowLoadError}</p>
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.comfyui.form.back}
      </button>
      <h1 className={styles.title}>
        {isEdit ? t.comfyui.form.editTitle : t.comfyui.form.newTitle}
        <span className={styles.experimentalBadge}>{t.comfyui.experimentalBadge}</span>
      </h1>

      {disconnectedNotice && (
        <div className={panelStyles.warningBox}>
          <p className={panelStyles.warningText}>
            {disconnectedNotice} <Link to="/settings#comfyui">{t.comfyui.list.disconnectedSettingsLink}</Link>
          </p>
        </div>
      )}

      <section className={styles.section}>
        <h2 className={styles.sectionHeading}>{t.comfyui.form.fileSectionHeading}</h2>
        <div className={styles.dropZone} onDrop={handleDrop} onDragOver={handleDragOver}>
          <p className={styles.dropZoneText}>
            {t.comfyui.form.dropZoneText}
          </p>
          <button type="button" className={styles.fileButton} onClick={() => fileInputRef.current?.click()}>
            {t.comfyui.form.chooseFile}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json"
            className={styles.hiddenFileInput}
            onChange={handleFileInputChange}
          />
          {template !== null && (
            <p className={styles.templateStatus}>
              {fmt(t.comfyui.form.templateLoaded, { nodeCount: nodes.length })}
            </p>
          )}
        </div>
        {fileError && <p className={styles.errorText}>{fileError}</p>}
        {warnings.length > 0 && (
          <ul className={styles.warningList}>
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
        {notice && <p className={styles.noticeText}>{notice}</p>}
      </section>

      {template !== null && (
        <>
          <section className={styles.section}>
            <h2 className={styles.sectionHeading}>{t.comfyui.form.basicInfoHeading}</h2>
            <div className={styles.basicGrid}>
              <div className={styles.field}>
                <label htmlFor="wf-name">{t.comfyui.form.nameLabel}</label>
                <input id="wf-name" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className={styles.field}>
                <label htmlFor="wf-operation" title={t.comfyui.form.operationTitle}>
                  {t.comfyui.form.operationLabel}
                </label>
                <select
                  id="wf-operation"
                  value={operation}
                  onChange={(e) => handleOperationChange(e.target.value as ComfyOperation)}
                >
                  {operationOptions(t).map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </section>

          <section className={styles.section}>
            <h2 className={styles.sectionHeading}>{t.comfyui.form.bindingsHeading}</h2>
            <div className={styles.bindingsGrid}>
              <NodeInputPicker
                label={t.comfyui.form.promptLabel}
                nodes={nodes}
                value={bindings.prompt}
                onChange={(ref) => setBindings((b) => ({ ...b, prompt: ref }))}
              />
              <NodeInputPicker
                label={t.comfyui.form.negativePromptLabel}
                nodes={nodes}
                value={bindings.negativePrompt}
                onChange={(ref) => setBindings((b) => ({ ...b, negativePrompt: ref }))}
              />
              <NodeInputPicker
                label={t.comfyui.form.widthLabel}
                nodes={nodes}
                value={bindings.width}
                onChange={(ref) => setBindings((b) => ({ ...b, width: ref }))}
              />
              <NodeInputPicker
                label={t.comfyui.form.heightLabel}
                nodes={nodes}
                value={bindings.height}
                onChange={(ref) => setBindings((b) => ({ ...b, height: ref }))}
              />
              <NodeInputPicker
                label={t.comfyui.form.batchSizeLabel}
                nodes={nodes}
                value={bindings.batchSize}
                onChange={(ref) => setBindings((b) => ({ ...b, batchSize: ref }))}
              />
            </div>

            {operation === 'edit' && (
              <div className={styles.subField}>
                <span className={styles.subFieldLabel}>{t.comfyui.form.imageSlotsLabel}</span>
                <p className={styles.helpText}>
                  {t.comfyui.form.imageSlotsHelp}
                </p>
                <div className={styles.seedList}>
                  {bindings.images.map((ref, index) => (
                    <div key={index} className={styles.seedRow}>
                      <NodeInputPicker
                        label={fmt(t.comfyui.form.imageSlotLabel, { index: index + 1 })}
                        nodes={nodes}
                        value={ref.node ? ref : null}
                        onChange={(next) =>
                          setBindings((b) => {
                            const images = [...b.images]
                            images[index] = next ?? { node: '', input: '' }
                            return { ...b, images }
                          })
                        }
                      />
                      <div className={styles.reorderButtons}>
                        <button
                          type="button"
                          aria-label={t.comfyui.form.moveUp}
                          disabled={index === 0}
                          onClick={() => setBindings((b) => ({ ...b, images: moveImageSlot(b.images, index, 'up') }))}
                        >
                          ▲
                        </button>
                        <button
                          type="button"
                          aria-label={t.comfyui.form.moveDown}
                          disabled={index === bindings.images.length - 1}
                          onClick={() =>
                            setBindings((b) => ({ ...b, images: moveImageSlot(b.images, index, 'down') }))
                          }
                        >
                          ▼
                        </button>
                      </div>
                      <button
                        type="button"
                        className={styles.smallRemoveButton}
                        onClick={() =>
                          setBindings((b) => ({ ...b, images: b.images.filter((_, i) => i !== index) }))
                        }
                      >
                        {t.comfyui.form.remove}
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className={styles.addButton}
                    onClick={() =>
                      setBindings((b) => ({ ...b, images: [...b.images, { node: '', input: '' }] }))
                    }
                  >
                    {t.comfyui.form.addImageSlot}
                  </button>
                </div>
              </div>
            )}

            <div className={styles.subField}>
              <span className={styles.subFieldLabel}>{t.comfyui.form.seedsLabel}</span>
              <div className={styles.seedList}>
                {bindings.seeds.map((seed, index) => (
                  <div key={index} className={styles.seedRow}>
                    <NodeInputPicker
                      label={fmt(t.comfyui.form.seedLabel, { index: index + 1 })}
                      nodes={nodes}
                      value={seed.node ? seed : null}
                      onChange={(ref) =>
                        setBindings((b) => {
                          const seeds = [...b.seeds]
                          seeds[index] = ref ?? { node: '', input: '' }
                          return { ...b, seeds }
                        })
                      }
                    />
                    <button
                      type="button"
                      className={styles.smallRemoveButton}
                      onClick={() =>
                        setBindings((b) => ({ ...b, seeds: b.seeds.filter((_, i) => i !== index) }))
                      }
                    >
                      {t.comfyui.form.remove}
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  className={styles.addButton}
                  onClick={() =>
                    setBindings((b) => ({ ...b, seeds: [...b.seeds, { node: '', input: '' }] }))
                  }
                >
                  {t.comfyui.form.addSeed}
                </button>
              </div>
            </div>

            {operation === 'edit' && (
              <div className={styles.subField}>
                <span className={styles.subFieldLabel}>{t.comfyui.form.maskLabel}</span>
                <div className={styles.maskModeRow}>
                  <label>
                    <input
                      type="radio"
                      name="mask-mode"
                      checked={bindings.maskMode === 'none'}
                      onChange={() => setBindings((b) => ({ ...b, maskMode: 'none', maskRef: null }))}
                    />
                    {t.comfyui.form.maskModeNone}
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="mask-mode"
                      checked={bindings.maskMode === 'load_image_mask'}
                      onChange={() => setBindings((b) => ({ ...b, maskMode: 'load_image_mask' }))}
                    />
                    {t.comfyui.form.maskModeLoadImageMask}
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="mask-mode"
                      checked={bindings.maskMode === 'image_alpha'}
                      onChange={() => setBindings((b) => ({ ...b, maskMode: 'image_alpha', maskRef: null }))}
                    />
                    {t.comfyui.form.maskModeImageAlpha}
                  </label>
                </div>
                {bindings.maskMode === 'load_image_mask' && (
                  <NodeInputPicker
                    label={t.comfyui.form.maskRefLabel}
                    nodes={nodes}
                    value={bindings.maskRef}
                    onChange={(ref) => setBindings((b) => ({ ...b, maskRef: ref }))}
                  />
                )}
              </div>
            )}

            <div className={styles.subField}>
              <NodeMultiPicker
                label={t.comfyui.form.outputsLabel}
                nodes={nodes}
                value={bindings.outputs}
                onChange={(outputs) => setBindings((b) => ({ ...b, outputs }))}
              />
            </div>
          </section>

          <section className={styles.section}>
            <h2 className={styles.sectionHeading}>{t.comfyui.form.exposedParamsHeading}</h2>
            <ExposedParamsTable
              rows={exposedRows}
              nodes={nodes}
              onChange={(index, row) =>
                setExposedRows((rows) => rows.map((r, i) => (i === index ? row : r)))
              }
            />
          </section>
        </>
      )}

      {clientErrors.length > 0 && (
        <ul className={styles.errorList}>
          {clientErrors.map((err) => (
            <li key={err}>{err}</li>
          ))}
        </ul>
      )}
      {serverErrorMessage && <p className={styles.errorText}>{serverErrorMessage}</p>}

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.saveButton}
          disabled={clientErrors.length > 0 || saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? t.comfyui.form.saving : isEdit ? t.comfyui.form.update : t.comfyui.form.register}
        </button>
      </div>

      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
  )
}

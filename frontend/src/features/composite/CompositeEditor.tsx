/**
 * 入力画像の配置(ADR-0050)。複数の画像を移動・回転・拡大縮小・四隅の変形・反転して1枚の
 * 入力にする全画面のモーダル。スケッチ(ADR-0010)と同じ作り(アイコンのボタンとツールチップ、
 * 保存とキャンセル、Esc は何も変えていないときだけ閉じる)。
 *
 * - 開き方は2つ。素材の Asset を並べて新しく始める(`new`)か、保存した配置をレシピから
 *   作り直す(`reopen`。保存は `replaces_asset_id` を付ける)。作り直しで読めない素材
 *   (論理削除など)は外して開き、そのことを示す。
 * - 状態(`EditorDoc`)は履歴(元に戻す・やり直す)で持つ。ドラッグ中の途中の状態は
 *   `draft` に置き、指を離したときに1回だけ履歴に積む。
 * - 表示と書き出しは同じ WebGL の描画(`renderer.ts`)。WebGL2 が使えなければ開かずに知らせる。
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { createCompositeAsset, getAsset, type AssetDetail } from '../../api/client'
import { Modal } from '../../components/Modal'
import { MODAL_BODY_ATTRIBUTE } from '../../components/modalFocus'
import { useModalFocus } from '../../components/useModalFocus'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { undoShortcutKind } from '../mask/undoShortcut'
import { CompositeAddDialog } from './CompositeAddDialog'
import { CompositeStage, type TransformMode } from './CompositeStage'
import {
  AddLayerIcon,
  BackwardIcon,
  CenterIcon,
  DeleteIcon,
  DistortModeIcon,
  DuplicateIcon,
  FitIcon,
  FlipXIcon,
  FlipYIcon,
  ForwardIcon,
  PrimaryIcon,
  RedoIcon,
  TransformModeIcon,
  UndoIcon,
} from './compositeIcons'
import {
  addLayers,
  centerLayer,
  createDocFromComposition,
  createDocFromSources,
  duplicateLayer,
  fitLayer,
  initHistory,
  moveLayer,
  nudgeLayer,
  pushHistory,
  redoHistory,
  removeLayer,
  resizeCanvas,
  setBackground,
  setLayerQuad,
  setPrimary,
  toggleFlip,
  undoHistory,
  type History,
  type LayerSource,
} from './editorDoc'
import { CompositeExportError, exportComposite } from './exportComposite'
import { applyNumericTransform, quadAngleDegrees, quadCenter, quadScalePercent, type Size } from './quad'
import {
  buildRecipe,
  clampCanvasSize,
  effectivePrimaryAssetId,
  MAX_CANVAS_EDGE,
  MAX_LAYERS,
  MIN_CANVAS_EDGE,
  parseComposition,
  recipeProblem,
  sourceAssetIdsInOrder,
  type EditorDoc,
} from './recipe'
import { isWebGL2Available } from './renderer'
import styles from './CompositeEditor.module.css'

export type CompositeEditorInit =
  | { kind: 'new'; sourceAssetIds: string[] }
  /** 保存した配置の作り直し。`assetId` の詳細(`composition` を含む)を取り直して開く。 */
  | { kind: 'reopen'; assetId: string }

export interface CompositeSaveResult {
  asset: AssetDetail
  /** 素材にした Asset(主たる素材が先頭、残りは足した順)。 */
  sourceAssetIds: string[]
  /** 「元の画像を入力から外す」を選んだか。 */
  removeSources: boolean
  /** 作り直しで置き換えた元の配置(新しく作ったときは null)。 */
  replacedAssetId: string | null
}

interface CompositeEditorProps {
  init: CompositeEditorInit
  /** 生成のフォームで選んでいるサイズ(auto などで決まらなければ null)。 */
  formSize?: Size | null
  /** 生成のフォームの入力の画像(「素材を足す」に出す)。 */
  inputAssetIds?: readonly string[]
  /** 「元の画像を入力から外す」を出すか(入力が1枚だけのプロバイダー・作り直しでは出さない)。 */
  showRemoveSourcesOption: boolean
  onSave: (result: CompositeSaveResult) => void
  onCancel: () => void
}

const PRESET_SIZES: readonly Size[] = [
  { width: 1024, height: 1024 },
  { width: 1536, height: 1024 },
  { width: 1024, height: 1536 },
  { width: 2048, height: 2048 },
]

function sizeKey(size: Size): string {
  return `${size.width}x${size.height}`
}

function isEditableTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement
  )
}

/** 数値の入力欄。入力中は下書きを持ち、Enter かフォーカスを外したときに確定する。 */
function NumberField({
  label,
  value,
  digits,
  min,
  max,
  onCommit,
}: {
  label: string
  value: number
  digits: number
  min?: number
  max?: number
  onCommit: (value: number) => void
}) {
  const id = useId()
  const formatted = value.toFixed(digits)
  const [draft, setDraft] = useState<string | null>(null)
  function commit() {
    if (draft === null) return
    const n = Number(draft)
    setDraft(null)
    if (draft.trim() === '' || !Number.isFinite(n)) return
    const clamped = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n))
    if (clamped.toFixed(digits) !== formatted) onCommit(clamped)
  }
  return (
    <label className={styles.numberField} htmlFor={id}>
      <span>{label}</span>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        step={digits === 0 ? 1 : 0.1}
        min={min}
        max={max}
        value={draft ?? formatted}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          }
        }}
      />
    </label>
  )
}

function ToolButton({
  label,
  onClick,
  disabled,
  selected,
  children,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  selected?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      className={styles.toolButton}
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      data-selected={selected}
      aria-pressed={selected}
    >
      {children}
    </button>
  )
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; replacedAssetId: string | null; firstSize: Size | null; notice: string | null }

export function CompositeEditor(props: CompositeEditorProps) {
  // WebGL2 が使えなければ、編集画面を開かずに知らせる(ADR-0050 Consequences)。
  const [webglOk] = useState(() => isWebGL2Available())
  const { t } = useI18n()
  if (!webglOk) {
    return (
      <Modal open title={t.composite.title} onClose={props.onCancel}>
        <p className={styles.unavailable}>{t.composite.webglUnavailable}</p>
      </Modal>
    )
  }
  return <CompositeEditorBody {...props} />
}

function CompositeEditorBody({
  init,
  formSize = null,
  inputAssetIds = [],
  showRemoveSourcesOption,
  onSave,
  onCancel,
}: CompositeEditorProps) {
  const { t } = useI18n()
  const c = t.composite
  const queryClient = useQueryClient()
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement | null>(null)

  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [history, setHistory] = useState<History<EditorDoc> | null>(null)
  const [draft, setDraft] = useState<EditorDoc | null>(null)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [mode, setMode] = useState<TransformMode>('transform')
  const [addOpen, setAddOpen] = useState(false)
  const [removeSources, setRemoveSources] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [rendererFailed, setRendererFailed] = useState(false)
  const [customSize, setCustomSize] = useState(false)
  const editedRef = useRef(false)

  async function fetchDetail(id: string): Promise<AssetDetail | null> {
    try {
      return await queryClient.fetchQuery({ queryKey: ['asset', id], queryFn: () => getAsset(id), staleTime: 0 })
    } catch {
      return null
    }
  }

  async function loadSources(ids: readonly string[]): Promise<{ sources: LayerSource[]; skipped: number }> {
    const details = await Promise.all(ids.map((id) => fetchDetail(id)))
    const sources: LayerSource[] = []
    let skipped = 0
    details.forEach((d) => {
      if (!d || d.deleted_at || d.kind === 'mask') skipped += 1
      else sources.push({ assetId: d.id, natural: { width: d.width, height: d.height } })
    })
    return { sources, skipped }
  }

  // 開いたときの準備(素材の大きさを引いて、並べる・レシピから作り直す)。
  useEffect(() => {
    let cancelled = false
    async function run() {
      if (init.kind === 'reopen') {
        const asset = await fetchDetail(init.assetId)
        if (cancelled) return
        if (!asset) {
          setLoad({ status: 'error' })
          return
        }
        const parsed = parseComposition(asset.composition)
        if (parsed) {
          const ids = [...new Set(parsed.layers.map((l) => l.assetId))]
          const { sources } = await loadSources(ids)
          if (cancelled) return
          const naturals = new Map(sources.map((s) => [s.assetId, s.natural]))
          const { doc, droppedCount } = createDocFromComposition(parsed, naturals)
          setHistory(initHistory(doc))
          setLoad({
            status: 'ready',
            replacedAssetId: asset.id,
            firstSize: null,
            notice: droppedCount > 0 ? fmt(c.droppedSources, { count: droppedCount }) : null,
          })
          return
        }
        // 記録が無ければ、この画像を素材にして新しく始める(置き換えはしない)。
        const canvas = clampCanvasSize({ width: asset.width, height: asset.height })
        const doc = createDocFromSources([{ assetId: asset.id, natural: { width: asset.width, height: asset.height } }], canvas)
        setHistory(initHistory(doc))
        setLoad({ status: 'ready', replacedAssetId: null, firstSize: canvas, notice: c.noComposition })
        return
      }
      const { sources, skipped } = await loadSources(init.sourceAssetIds)
      if (cancelled) return
      const firstSize = sources[0] ? clampCanvasSize(sources[0].natural) : null
      const canvas = firstSize ?? (formSize ? clampCanvasSize(formSize) : { width: 1024, height: 1024 })
      const doc = createDocFromSources(sources, canvas)
      setHistory(initHistory(doc))
      const over = Math.max(0, sources.length - MAX_LAYERS)
      setLoad({
        status: 'ready',
        replacedAssetId: null,
        firstSize,
        notice:
          skipped > 0
            ? fmt(c.skippedSources, { count: skipped })
            : over > 0
              ? fmt(c.rejectedLayers, { count: over })
              : null,
      })
    }
    void run()
    return () => {
      cancelled = true
    }
    // init はこのモーダルの生存期間中は変わらない前提。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (load.status === 'ready') setNotice(load.notice)
  }, [load])

  const doc = draft ?? history?.present ?? null
  const selected = doc?.layers.find((l) => l.key === selectedKey) ?? null

  function commit(next: EditorDoc) {
    setDraft(null)
    setHistory((h) => (h ? pushHistory(h, next) : h))
    editedRef.current = true
    setError(null)
  }

  function apply(f: (d: EditorDoc) => EditorDoc) {
    if (!history) return
    commit(f(history.present))
  }

  function undo() {
    setDraft(null)
    setHistory((h) => (h ? undoHistory(h) : h))
  }
  function redo() {
    setDraft(null)
    setHistory((h) => (h ? redoHistory(h) : h))
  }

  // Ctrl/Cmd+Z・Shift+Ctrl/Cmd+Z・Ctrl+Y。入力欄の中では入力欄の undo に任せる。
  const shortcutRef = useRef({ undo, redo, active: true })
  useEffect(() => {
    shortcutRef.current = { undo, redo, active: !addOpen }
  })
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.isComposing || !shortcutRef.current.active || isEditableTarget(e.target)) return
      const kind = undoShortcutKind(e)
      if (!kind) return
      e.preventDefault()
      e.stopPropagation()
      if (kind === 'undo') shortcutRef.current.undo()
      else shortcutRef.current.redo()
    }
    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [])

  useModalFocus(panelRef, true, {
    onEscape: () => {
      if (!editedRef.current) onCancel()
    },
  })

  function handleKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (!selected || isEditableTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault()
      apply((d) => removeLayer(d, selected.key))
      setSelectedKey(null)
      return
    }
    const step = e.shiftKey ? 10 : 1
    const deltas: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }
    const delta = deltas[e.key]
    if (delta) {
      e.preventDefault()
      apply((d) => nudgeLayer(d, selected.key, delta[0], delta[1]))
    }
  }

  async function handleAdd(ids: string[]) {
    setAddOpen(false)
    if (!history) return
    const { sources, skipped } = await loadSources(ids)
    const result = addLayers(history.present, sources)
    commit(result.doc)
    if (result.added[0]) setSelectedKey(result.added[result.added.length - 1].key)
    setNotice(
      result.rejectedCount > 0
        ? fmt(c.rejectedLayers, { count: result.rejectedCount })
        : skipped > 0
          ? fmt(c.skippedSources, { count: skipped })
          : null,
    )
  }

  // 素材の一覧のサムネイル(レーティングのマスクを掛けるため、詳細を引く)。
  const layerAssetIds = doc ? [...new Set(doc.layers.map((l) => l.assetId))] : []
  const detailQueries = useQueries({
    queries: layerAssetIds.map((id) => ({ queryKey: ['asset', id], queryFn: () => getAsset(id) })),
  })
  const detailById = new Map<string, AssetDetail>()
  layerAssetIds.forEach((id, i) => {
    const data = detailQueries[i]?.data
    if (data) detailById.set(id, data)
  })

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!doc || load.status !== 'ready') throw new Error(c.saveFailedDefault)
      const recipe = buildRecipe(doc)
      if (!recipe) throw new Error(c.saveFailedDefault)
      let blob: Blob
      try {
        blob = await exportComposite(doc)
      } catch (err) {
        if (err instanceof CompositeExportError) {
          throw new Error(
            err.reason === 'tooLarge' ? c.canvasTooLarge : err.reason === 'load' ? c.loadError : c.exportError,
          )
        }
        throw new Error(c.exportError)
      }
      const asset = await createCompositeAsset(blob, recipe, load.replacedAssetId ?? undefined)
      return { asset, sourceAssetIds: sourceAssetIdsInOrder(doc), replacedAssetId: load.replacedAssetId }
    },
    onSuccess: ({ asset, sourceAssetIds, replacedAssetId }) => {
      queryClient.setQueryData(['asset', asset.id], asset)
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['lineage'] })
      if (replacedAssetId) queryClient.invalidateQueries({ queryKey: ['asset', replacedAssetId] })
      onSave({ asset, sourceAssetIds, removeSources: showRemoveSourcesOption && removeSources, replacedAssetId })
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : c.saveFailedDefault),
  })

  const problem = doc ? recipeProblem(doc) : null
  const problemText =
    problem === 'noLayers'
      ? c.problems.noLayers
      : problem === 'tooManyLayers'
        ? fmt(c.problems.tooManyLayers, { max: MAX_LAYERS })
        : problem === 'invalidShape'
          ? c.problems.invalidShape
          : problem === 'outOfBounds'
            ? c.problems.outOfBounds
            : problem === 'invalidCanvas'
            ? fmt(c.problems.invalidCanvas, { min: MIN_CANVAS_EDGE, max: MAX_CANVAS_EDGE })
            : null

  // キャンバスの大きさの選択肢。
  const sizeOptions: { key: string; label: string; size: Size }[] = []
  if (load.status === 'ready' && load.firstSize) {
    sizeOptions.push({ key: `first:${sizeKey(load.firstSize)}`, label: fmt(c.panel.sizeFirst, { ...load.firstSize }), size: load.firstSize })
  }
  if (formSize) {
    const s = clampCanvasSize(formSize)
    sizeOptions.push({ key: `form:${sizeKey(s)}`, label: fmt(c.panel.sizeForm, { ...s }), size: s })
  }
  for (const s of PRESET_SIZES) sizeOptions.push({ key: sizeKey(s), label: `${s.width}×${s.height}`, size: s })
  // 今の大きさがどの選択肢にも無い(作り直しで開いた・カスタムで決めた)ときは、その大きさも選択肢に出す。
  if (doc && !sizeOptions.some((o) => sizeKey(o.size) === sizeKey(doc.canvas))) {
    sizeOptions.unshift({ key: `current:${sizeKey(doc.canvas)}`, label: `${doc.canvas.width}×${doc.canvas.height}`, size: doc.canvas })
  }
  const currentOption = doc && !customSize ? sizeOptions.find((o) => sizeKey(o.size) === sizeKey(doc.canvas)) : undefined
  const sizeSelectValue = currentOption?.key ?? 'custom'

  function changeCanvas(size: Size) {
    apply((d) => resizeCanvas(d, size))
  }

  const primaryAssetId = doc ? effectivePrimaryAssetId(doc) : null
  const selectedIndex = doc && selected ? doc.layers.indexOf(selected) : -1
  const busy = saveMutation.isPending

  return (
    <div className={styles.backdrop}>
      <div
        ref={panelRef}
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <div className={styles.header}>
          <h2 id={titleId} className={styles.title}>
            {c.title}
          </h2>
          <button type="button" className={styles.closeButton} onClick={onCancel} aria-label={c.close} title={c.close}>
            ✕
          </button>
        </div>

        <div className={styles.toolbar} {...{ [MODAL_BODY_ATTRIBUTE]: '' }}>
          <div className={styles.toolGroup}>
            <ToolButton
              label={c.toolbar.add}
              onClick={() => setAddOpen(true)}
              disabled={!doc || doc.layers.length >= MAX_LAYERS}
            >
              <AddLayerIcon />
            </ToolButton>
          </div>
          <div className={styles.toolGroup} role="group" aria-label={c.toolbar.modeGroup}>
            <ToolButton label={c.toolbar.modeTransform} onClick={() => setMode('transform')} selected={mode === 'transform'}>
              <TransformModeIcon />
            </ToolButton>
            <ToolButton label={c.toolbar.modeDistort} onClick={() => setMode('distort')} selected={mode === 'distort'}>
              <DistortModeIcon />
            </ToolButton>
          </div>
          <div className={styles.toolGroup}>
            <ToolButton label={c.toolbar.flipX} disabled={!selected} onClick={() => selected && apply((d) => toggleFlip(d, selected.key, 'x'))}>
              <FlipXIcon />
            </ToolButton>
            <ToolButton label={c.toolbar.flipY} disabled={!selected} onClick={() => selected && apply((d) => toggleFlip(d, selected.key, 'y'))}>
              <FlipYIcon />
            </ToolButton>
            <ToolButton
              label={c.toolbar.forward}
              disabled={!selected || !doc || selectedIndex >= doc.layers.length - 1}
              onClick={() => selected && apply((d) => moveLayer(d, selected.key, 'forward'))}
            >
              <ForwardIcon />
            </ToolButton>
            <ToolButton
              label={c.toolbar.backward}
              disabled={!selected || selectedIndex <= 0}
              onClick={() => selected && apply((d) => moveLayer(d, selected.key, 'backward'))}
            >
              <BackwardIcon />
            </ToolButton>
            <ToolButton
              label={c.toolbar.duplicate}
              disabled={!selected || !doc || doc.layers.length >= MAX_LAYERS}
              onClick={() => {
                if (!selected || !history) return
                const r = duplicateLayer(history.present, selected.key)
                commit(r.doc)
                if (r.newKey) setSelectedKey(r.newKey)
              }}
            >
              <DuplicateIcon />
            </ToolButton>
            <ToolButton
              label={c.toolbar.setPrimary}
              disabled={!selected || selected.assetId === primaryAssetId}
              onClick={() => selected && apply((d) => setPrimary(d, selected.assetId))}
            >
              <PrimaryIcon />
            </ToolButton>
            <ToolButton
              label={c.toolbar.remove}
              disabled={!selected}
              onClick={() => {
                if (!selected) return
                apply((d) => removeLayer(d, selected.key))
                setSelectedKey(null)
              }}
            >
              <DeleteIcon />
            </ToolButton>
          </div>
          <div className={styles.toolGroup}>
            <ToolButton label={c.toolbar.fit} disabled={!selected} onClick={() => selected && apply((d) => fitLayer(d, selected.key))}>
              <FitIcon />
            </ToolButton>
            <ToolButton label={c.toolbar.center} disabled={!selected} onClick={() => selected && apply((d) => centerLayer(d, selected.key))}>
              <CenterIcon />
            </ToolButton>
          </div>
          <div className={styles.toolGroup}>
            <ToolButton label={c.toolbar.undo} disabled={!history || history.past.length === 0} onClick={undo}>
              <UndoIcon />
            </ToolButton>
            <ToolButton label={c.toolbar.redo} disabled={!history || history.future.length === 0} onClick={redo}>
              <RedoIcon />
            </ToolButton>
          </div>
        </div>

        <div className={styles.body}>
          <div className={styles.stageArea}>
            {load.status === 'loading' && <p className={styles.loading}>{c.loading}</p>}
            {load.status === 'error' && <p className={styles.noteText}>{c.loadError}</p>}
            {rendererFailed && <p className={styles.noteText}>{c.webglUnavailable}</p>}
            {doc && !rendererFailed && (
              <CompositeStage
                doc={doc}
                selectedKey={selectedKey}
                mode={mode}
                onSelect={setSelectedKey}
                onPreview={setDraft}
                onCommit={commit}
                onRendererError={() => setRendererFailed(true)}
                onTextureError={() => setNotice(c.textureError)}
              />
            )}
          </div>

          <aside className={styles.sidePanel}>
            <p className={styles.hint}>{mode === 'distort' ? c.distortHint : c.hint}</p>

            {doc && (
              <section className={styles.section}>
                <h3 className={styles.sectionHeading}>{c.panel.canvasHeading}</h3>
                <label className={styles.fieldRow}>
                  <span>{c.panel.size}</span>
                  <select
                    value={sizeSelectValue}
                    onChange={(e) => {
                      if (e.target.value === 'custom') {
                        setCustomSize(true)
                        return
                      }
                      setCustomSize(false)
                      const option = sizeOptions.find((o) => o.key === e.target.value)
                      if (option) changeCanvas(option.size)
                    }}
                  >
                    {sizeOptions.map((o) => (
                      <option key={o.key} value={o.key}>
                        {o.label}
                      </option>
                    ))}
                    <option value="custom">{c.panel.sizeCustom}</option>
                  </select>
                </label>
                {sizeSelectValue === 'custom' && (
                  <div className={styles.numberGrid}>
                    <NumberField
                      label={c.panel.width}
                      value={doc.canvas.width}
                      digits={0}
                      min={MIN_CANVAS_EDGE}
                      max={MAX_CANVAS_EDGE}
                      onCommit={(w) => changeCanvas({ width: Math.round(w), height: doc.canvas.height })}
                    />
                    <NumberField
                      label={c.panel.height}
                      value={doc.canvas.height}
                      digits={0}
                      min={MIN_CANVAS_EDGE}
                      max={MAX_CANVAS_EDGE}
                      onCommit={(h) => changeCanvas({ width: doc.canvas.width, height: Math.round(h) })}
                    />
                  </div>
                )}
                <div className={styles.fieldRow} role="radiogroup" aria-label={c.panel.background}>
                  <span>{c.panel.background}</span>
                  <div className={styles.segmented}>
                    {(['white', 'transparent'] as const).map((bg) => (
                      <button
                        key={bg}
                        type="button"
                        role="radio"
                        aria-checked={doc.background === bg}
                        data-selected={doc.background === bg}
                        onClick={() => doc.background !== bg && apply((d) => setBackground(d, bg))}
                      >
                        {bg === 'white' ? c.panel.backgroundWhite : c.panel.backgroundTransparent}
                      </button>
                    ))}
                  </div>
                </div>
              </section>
            )}

            {doc && (
              <section className={styles.section}>
                <h3 className={styles.sectionHeading}>{c.panel.selectedHeading}</h3>
                {selected ? (
                  <div className={styles.numberGrid}>
                    <NumberField
                      label={c.panel.centerX}
                      value={quadCenter(selected.quad).x}
                      digits={0}
                      onCommit={(v) =>
                        apply((d) => setLayerQuad(d, selected.key, applyNumericTransform(selected.quad, selected.natural, { centerX: v })))
                      }
                    />
                    <NumberField
                      label={c.panel.centerY}
                      value={quadCenter(selected.quad).y}
                      digits={0}
                      onCommit={(v) =>
                        apply((d) => setLayerQuad(d, selected.key, applyNumericTransform(selected.quad, selected.natural, { centerY: v })))
                      }
                    />
                    <NumberField
                      label={c.panel.scale}
                      value={quadScalePercent(selected.quad, selected.natural)}
                      digits={1}
                      min={0.1}
                      max={10000}
                      onCommit={(v) =>
                        apply((d) =>
                          setLayerQuad(d, selected.key, applyNumericTransform(selected.quad, selected.natural, { scalePercent: v })),
                        )
                      }
                    />
                    <NumberField
                      label={c.panel.angle}
                      value={quadAngleDegrees(selected.quad)}
                      digits={1}
                      min={-360}
                      max={360}
                      onCommit={(v) =>
                        apply((d) => setLayerQuad(d, selected.key, applyNumericTransform(selected.quad, selected.natural, { angleDeg: v })))
                      }
                    />
                  </div>
                ) : (
                  <p className={styles.mutedText}>{c.panel.noSelection}</p>
                )}
              </section>
            )}

            {doc && (
              <section className={styles.section}>
                <h3 className={styles.sectionHeading}>
                  {fmt(c.panel.layersHeading, { count: doc.layers.length, max: MAX_LAYERS })}
                </h3>
                {doc.layers.length === 0 ? (
                  <p className={styles.mutedText}>{c.panel.emptyLayers}</p>
                ) : (
                  <ul className={styles.layerList}>
                    {/* 上に重なっているものを上に出す。 */}
                    {[...doc.layers].reverse().map((l) => {
                      const detail = detailById.get(l.assetId)
                      const isPrimary = l.assetId === primaryAssetId
                      const zIndex = doc.layers.indexOf(l) + 1
                      return (
                        <li key={l.key}>
                          <button
                            type="button"
                            className={styles.layerRow}
                            data-selected={l.key === selectedKey}
                            aria-pressed={l.key === selectedKey}
                            onClick={() => setSelectedKey(l.key === selectedKey ? null : l.key)}
                          >
                            <span className={styles.thumbFrame}>
                              <AssetThumb
                                assetId={l.assetId}
                                rating={detail ? (detail.content_rating ?? null) : undefined}
                                withheld={detail?.content_withheld === true}
                                className={`${styles.thumbImage} checkerboard`}
                                alt=""
                                draggable={false}
                              />
                            </span>
                            <span className={styles.layerLabel}>
                              {fmt(c.panel.layerLabel, { n: zIndex })}
                              {(l.flipX || l.flipY) && <span className={styles.layerMeta}>{c.panel.flipped}</span>}
                            </span>
                            {isPrimary && (
                              <span className={styles.primaryBadge} title={c.panel.primaryTitle}>
                                {c.panel.primaryBadge}
                              </span>
                            )}
                          </button>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </section>
            )}
          </aside>
        </div>

        <div className={styles.footer}>
          {notice && <p className={styles.noteText}>{notice}</p>}
          {(error || problemText) && <p className={styles.errorText}>{error ?? problemText}</p>}
          {showRemoveSourcesOption && (
            <label className={styles.checkboxLabel}>
              <input type="checkbox" checked={removeSources} onChange={(e) => setRemoveSources(e.target.checked)} />
              {c.removeSources}
            </label>
          )}
          <button type="button" className={styles.secondaryButton} onClick={onCancel}>
            {c.cancel}
          </button>
          <button
            type="button"
            className={styles.primaryButton}
            onClick={() => saveMutation.mutate()}
            disabled={busy || !doc || problem !== null || rendererFailed}
          >
            {busy ? c.saving : c.save}
          </button>
        </div>
      </div>

      <CompositeAddDialog
        open={addOpen}
        inputAssetIds={inputAssetIds}
        remaining={doc ? Math.max(0, MAX_LAYERS - doc.layers.length) : 0}
        onAdd={(ids) => void handleAdd(ids)}
        onClose={() => setAddOpen(false)}
      />
    </div>
  )
}

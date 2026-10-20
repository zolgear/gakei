/**
 * 領域の指示(ADR-0053)の編集画面。主たる入力(1枚目)に四角を描き、四角ごとに種類・指示・描く文字・
 * 参照画像を書く全画面のモーダル。配置(ADR-0050)・スケッチ(ADR-0010)と同じ作り(アイコンのボタンと
 * ツールチップ、保存とキャンセル、Esc は何も変えていないときだけ閉じる)。
 *
 * - 状態(四角の並び。並び順が番号)は履歴(元に戻す・やり直す)で持つ。ドラッグ中は `draft` に置き、
 *   指を離したときに1回だけ履歴に積む。文字の入力は同じ欄が続く間は1つにまとめる。
 * - 検出(7章)は `useRegionDetection`。候補を押すと四角になる。候補は保存しない(履歴はサーバー)。
 * - 保存すると四角を API の形(番号を 1 から振り直す)でフォームに返す。キャンセルは捨てる。
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useQueries } from '@tanstack/react-query'
import { assetUrl } from '../../api/assetUrl'
import { getAsset, type AssetDetail, type RegionBox, type RegionKind } from '../../api/client'
import { MODAL_BODY_ATTRIBUTE } from '../../components/modalFocus'
import { useModalFocus } from '../../components/useModalFocus'
import { fmt, intlLocale, useI18n, type Messages } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { DeleteIcon, FitIcon, RedoIcon, UndoIcon } from '../composite/compositeIcons'
import { initHistory, pushHistory, redoHistory, undoHistory, type History } from '../composite/editorDoc'
import { undoShortcutKind } from '../mask/undoShortcut'
import { ArrowDownIcon, ArrowUpIcon, PanIcon, ZoomInIcon, ZoomOutIcon } from './regionIcons'
import { RegionReferenceDialog } from './RegionReferenceDialog'
import { RegionStage } from './RegionStage'
import {
  addRegion,
  boxesFromEditorRegions,
  canSaveRegions,
  editorRegionsFromBoxes,
  moveBox,
  moveRegionOrder,
  REGION_INSTRUCTION_MAX_LENGTH,
  REGION_KINDS,
  REGION_TEXT_MAX_LENGTH,
  REGIONS_MAX,
  regionBadgeTextColor,
  regionColor,
  regionExtraInputCount,
  regionProblems,
  removeRegion,
  setRegionKind,
  updateRegion,
  type EditorRegion,
  type RegionProblem,
} from './regions'
import { useRegionDetection, type RegionCandidate } from './useRegionDetection'
import styles from './RegionEditor.module.css'

export interface RegionEditorProps {
  /** 主たる入力(1枚目)。 */
  asset: AssetDetail
  initialRegions: readonly RegionBox[]
  /** 生成のフォームの入力の画像(参照画像の選択と、入力の枚数の確かめに使う)。 */
  inputAssetIds: readonly string[]
  maxInputImages: number
  /** 四角の渡し方(`image_and_text` なら、効き方の保証が無いことを示す)。 */
  delivery: string | null
  onSave: (regions: RegionBox[]) => void
  onCancel: () => void
}

const ZOOM_STEPS = [1, 1.5, 2, 3, 4]
/** 矢印キーで動かす量(割合。Shift で大きく)。 */
const NUDGE = 0.005
const NUDGE_LARGE = 0.03

function isEditableTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement
  )
}

function problemText(t: Messages, problem: RegionProblem): string {
  const p = t.regions.problems
  switch (problem) {
    case 'instructionRequired':
      return p.instructionRequired
    case 'instructionTooLong':
      return fmt(p.instructionTooLong, { max: REGION_INSTRUCTION_MAX_LENGTH })
    case 'textTooLong':
      return fmt(p.textTooLong, { max: REGION_TEXT_MAX_LENGTH })
    case 'invalidBox':
      return p.invalidBox
    case 'invalidSourceBox':
      return p.invalidSourceBox
  }
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

export function RegionEditor({
  asset,
  initialRegions,
  inputAssetIds,
  maxInputImages,
  delivery,
  onSave,
  onCancel,
}: RegionEditorProps) {
  const { t, locale } = useI18n()
  const r = t.regions
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement | null>(null)
  const cardRefs = useRef(new Map<string, HTMLLIElement>())

  const [history, setHistory] = useState<History<EditorRegion[]>>(() => initHistory(editorRegionsFromBoxes(initialRegions)))
  const [draft, setDraft] = useState<EditorRegion[] | null>(null)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1)
  const [panMode, setPanMode] = useState(false)
  const [referenceKey, setReferenceKey] = useState<string | null>(null)
  const [imageError, setImageError] = useState(false)
  const [detectQuery, setDetectQuery] = useState('')
  const editedRef = useRef(false)
  const lastCoalesceRef = useRef<string | null>(null)

  const detection = useRegionDetection(asset.id)
  const regions = draft ?? history.present
  const selected = regions.find((x) => x.key === selectedKey) ?? null

  function commit(next: EditorRegion[], coalesce?: string) {
    // 同じ欄の文字の入力が続く間は、履歴を1つにまとめる(打鍵ごとに元に戻すの段を作らない)。
    const replace = coalesce !== undefined && lastCoalesceRef.current === coalesce
    lastCoalesceRef.current = coalesce ?? null
    setDraft(null)
    setHistory((h) => (replace && h.past.length > 0 ? { ...h, present: next, future: [] } : pushHistory(h, next)))
    editedRef.current = true
  }

  function apply(f: (current: EditorRegion[]) => EditorRegion[], coalesce?: string) {
    commit(f(history.present), coalesce)
  }

  function undo() {
    lastCoalesceRef.current = null
    setDraft(null)
    setHistory((h) => undoHistory(h))
  }
  function redo() {
    lastCoalesceRef.current = null
    setDraft(null)
    setHistory((h) => redoHistory(h))
  }

  // Ctrl/Cmd+Z・Shift+Ctrl/Cmd+Z・Ctrl+Y。入力欄の中では入力欄の undo に任せる。
  const shortcutRef = useRef({ undo, redo, active: true })
  useEffect(() => {
    shortcutRef.current = { undo, redo, active: referenceKey === null }
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

  // 画像の上で四角を選んだら、その札を見える所へ出す。
  useEffect(() => {
    if (!selectedKey) return
    cardRefs.current.get(selectedKey)?.scrollIntoView({ block: 'nearest' })
  }, [selectedKey])

  function handleKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (!selected || isEditableTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault()
      apply((cur) => removeRegion(cur, selected.key))
      setSelectedKey(null)
      return
    }
    const step = e.shiftKey ? NUDGE_LARGE : NUDGE
    const deltas: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }
    const delta = deltas[e.key]
    if (delta) {
      e.preventDefault()
      apply((cur) => updateRegion(cur, selected.key, { box: moveBox(selected.box, delta[0], delta[1]) }))
    }
  }

  function adoptCandidate(candidate: RegionCandidate) {
    if (history.present.length >= REGIONS_MAX) return
    const next = addRegion(history.present, candidate.box, { hint: candidate.label })
    commit(next)
    setSelectedKey(next[next.length - 1].key)
    detection.markAdopted(candidate.key)
  }

  function zoomBy(direction: 1 | -1) {
    const index = ZOOM_STEPS.findIndex((z) => z >= zoom)
    const nextIndex = Math.min(ZOOM_STEPS.length - 1, Math.max(0, (index < 0 ? 0 : index) + direction))
    const next = ZOOM_STEPS[nextIndex]
    setZoom(next)
    if (next === 1) setPanMode(false)
  }

  // 参照画像のサムネイル(レーティングのマスクを掛けるため、詳細を引く)。
  const referenceIds = [...new Set(regions.map((x) => x.referenceAssetId).filter((id): id is string => id !== null))]
  const referenceQueries = useQueries({
    queries: referenceIds.map((id) => ({ queryKey: ['asset', id], queryFn: () => getAsset(id) })),
  })
  const referenceDetails = new Map<string, AssetDetail>()
  referenceIds.forEach((id, i) => {
    const data = referenceQueries[i]?.data
    if (data) referenceDetails.set(id, data)
  })

  const saveable = canSaveRegions(regions)
  const boxesNow = boxesFromEditorRegions(regions)
  const extra = regionExtraInputCount(boxesNow, inputAssetIds)
  const totalInputs = inputAssetIds.length + extra
  const atLimit = regions.length >= REGIONS_MAX
  const referenceRegion = regions.find((x) => x.key === referenceKey) ?? null

  function historyLabel(index: number): string {
    const record = detection.history[index]
    const time = new Date(record.created_at)
    const timeText = Number.isNaN(time.getTime())
      ? record.created_at
      : time.toLocaleString(intlLocale(locale), { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    return fmt(r.detection.historyEntry, {
      query: record.query?.trim() || r.detection.historyAll,
      time: timeText,
      count: record.regions.length,
    })
  }

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
            {r.title}
          </h2>
          <button type="button" className={styles.closeButton} onClick={onCancel} aria-label={r.close} title={r.close}>
            ✕
          </button>
        </div>

        <div className={styles.toolbar} {...{ [MODAL_BODY_ATTRIBUTE]: '' }}>
          <div className={styles.toolGroup}>
            <ToolButton label={r.toolbar.undo} disabled={history.past.length === 0} onClick={undo}>
              <UndoIcon />
            </ToolButton>
            <ToolButton label={r.toolbar.redo} disabled={history.future.length === 0} onClick={redo}>
              <RedoIcon />
            </ToolButton>
          </div>
          <div className={styles.toolGroup}>
            <ToolButton label={r.toolbar.zoomOut} disabled={zoom <= ZOOM_STEPS[0]} onClick={() => zoomBy(-1)}>
              <ZoomOutIcon />
            </ToolButton>
            <ToolButton
              label={r.toolbar.fit}
              disabled={zoom === 1}
              onClick={() => {
                setZoom(1)
                setPanMode(false)
              }}
            >
              <FitIcon />
            </ToolButton>
            <ToolButton label={r.toolbar.zoomIn} disabled={zoom >= ZOOM_STEPS[ZOOM_STEPS.length - 1]} onClick={() => zoomBy(1)}>
              <ZoomInIcon />
            </ToolButton>
            <ToolButton label={r.toolbar.pan} selected={panMode} disabled={zoom === 1} onClick={() => setPanMode((v) => !v)}>
              <PanIcon />
            </ToolButton>
            <span className={styles.zoomText}>{Math.round(zoom * 100)}%</span>
          </div>
          <div className={styles.toolGroup}>
            <ToolButton
              label={r.toolbar.remove}
              disabled={!selected}
              onClick={() => {
                if (!selected) return
                apply((cur) => removeRegion(cur, selected.key))
                setSelectedKey(null)
              }}
            >
              <DeleteIcon />
            </ToolButton>
          </div>
          <span className={styles.countText}>{fmt(r.count, { count: regions.length, max: REGIONS_MAX })}</span>
        </div>

        <div className={styles.body}>
          <div
            className={styles.stageArea}
            // 狭い幅では、画像の縦横比に合わせた高さにする(横長の画像で上下に空きが出ないように)。
            style={{ '--regions-aspect': `${asset.width} / ${asset.height}` } as React.CSSProperties}
          >
            {imageError ? (
              <p className={styles.noteText}>{r.loadError}</p>
            ) : (
              <RegionStage
                imageUrl={assetUrl(asset.id, 'preview')}
                natural={{ width: asset.width, height: asset.height }}
                regions={regions}
                selectedKey={selectedKey}
                candidates={detection.candidates}
                zoom={zoom}
                panMode={panMode}
                canAdd={!atLimit}
                labels={{ badge: (n) => fmt(r.card.aria, { n }) }}
                onSelect={setSelectedKey}
                onPreview={setDraft}
                onCommit={(next, selectKey) => {
                  commit(next)
                  if (selectKey) setSelectedKey(selectKey)
                }}
                onAdoptCandidate={adoptCandidate}
                onImageError={() => setImageError(true)}
              />
            )}
          </div>

          <aside className={styles.sidePanel}>
            <p className={styles.hint}>{r.hint}</p>
            {atLimit && <p className={styles.noteText}>{fmt(r.limitReached, { max: REGIONS_MAX })}</p>}

            <DetectionSection
              detection={detection}
              query={detectQuery}
              onQueryChange={setDetectQuery}
              historyLabel={historyLabel}
            />

            {regions.length === 0 ? (
              <p className={styles.mutedText}>{r.empty}</p>
            ) : (
              <ol className={styles.cardList}>
                {regions.map((region, index) => (
                  <RegionCard
                    key={region.key}
                    ref={(el) => {
                      if (el) cardRefs.current.set(region.key, el)
                      else cardRefs.current.delete(region.key)
                    }}
                    region={region}
                    index={index}
                    total={regions.length}
                    selected={region.key === selectedKey}
                    referenceDetail={region.referenceAssetId ? referenceDetails.get(region.referenceAssetId) : undefined}
                    onSelect={() => setSelectedKey(region.key)}
                    onKind={(kind) => apply((cur) => setRegionKind(cur, region.key, kind))}
                    onInstruction={(value) =>
                      apply((cur) => updateRegion(cur, region.key, { instruction: value }), `${region.key}:instruction`)
                    }
                    onText={(value) => apply((cur) => updateRegion(cur, region.key, { text: value }), `${region.key}:text`)}
                    onPickReference={() => setReferenceKey(region.key)}
                    onClearReference={() => apply((cur) => updateRegion(cur, region.key, { referenceAssetId: null }))}
                    onMove={(direction) => apply((cur) => moveRegionOrder(cur, region.key, direction))}
                    onRemove={() => {
                      apply((cur) => removeRegion(cur, region.key))
                      if (selectedKey === region.key) setSelectedKey(null)
                    }}
                  />
                ))}
              </ol>
            )}
          </aside>
        </div>

        <div className={styles.footer}>
          <div className={styles.footerNotes}>
            {delivery === 'image_and_text' && <p className={styles.mutedNote}>{r.notes.imageAndText}</p>}
            <p className={styles.mutedNote}>{fmt(r.notes.countsTowardLimit, { max: maxInputImages })}</p>
            {regions.length > 0 && totalInputs > maxInputImages && (
              <p className={styles.noteText}>
                {fmt(t.regions.form.tooManyInputs, { extra, count: totalInputs, max: maxInputImages })}
              </p>
            )}
            {!saveable && <p className={styles.errorText}>{r.saveBlocked}</p>}
          </div>
          <div className={styles.footerButtons}>
            <button type="button" className={styles.secondaryButton} onClick={onCancel}>
              {r.cancel}
            </button>
            <button
              type="button"
              className={styles.primaryButton}
              disabled={!saveable}
              onClick={() => onSave(boxesFromEditorRegions(history.present))}
            >
              {r.save}
            </button>
          </div>
        </div>
      </div>

      {referenceRegion && (
        <RegionReferenceDialog
          open
          inputAssetIds={inputAssetIds}
          currentAssetId={referenceRegion.referenceAssetId}
          onChoose={(id) => {
            apply((cur) => updateRegion(cur, referenceRegion.key, { referenceAssetId: id }))
            setReferenceKey(null)
          }}
          onClose={() => setReferenceKey(null)}
        />
      )}
    </div>
  )
}

function DetectionSection({
  detection,
  query,
  onQueryChange,
  historyLabel,
}: {
  detection: ReturnType<typeof useRegionDetection>
  query: string
  onQueryChange: (q: string) => void
  historyLabel: (index: number) => string
}) {
  const { t } = useI18n()
  const d = t.regions.detection
  const queryId = useId()
  const historyId = useId()
  // 検出が使えず、履歴も無ければ出さない(手で描くことはできる)。
  if (!detection.usable && detection.history.length === 0) return null
  return (
    <section className={styles.section}>
      <h3 className={styles.sectionHeading}>{d.heading}</h3>
      {detection.usable && (
        <>
          <p className={styles.hint}>{d.intro}</p>
          <label className={styles.fieldLabel} htmlFor={queryId}>
            {d.queryLabel}
          </label>
          <div className={styles.detectRow}>
            <input
              id={queryId}
              type="text"
              className={styles.textInput}
              value={query}
              maxLength={200}
              placeholder={d.queryPlaceholder}
              disabled={detection.running}
              onChange={(e) => onQueryChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  if (!detection.running) detection.run(query)
                }
              }}
            />
            {detection.running ? (
              <button type="button" className={styles.secondaryButton} onClick={detection.cancel}>
                {d.cancel}
              </button>
            ) : (
              <button type="button" className={styles.primaryButton} onClick={() => detection.run(query)}>
                {d.run}
              </button>
            )}
          </div>
          {detection.running && (
            <p className={styles.mutedText} role="status">
              {d.running}
            </p>
          )}
        </>
      )}

      {detection.history.length > 0 && (
        <div className={styles.historyRow}>
          <label className={styles.fieldLabel} htmlFor={historyId}>
            {d.historyLabel}
          </label>
          <div className={styles.detectRow}>
            <select
              id={historyId}
              className={styles.select}
              value={detection.selectedId ?? ''}
              onChange={(e) => detection.select(e.target.value || null)}
            >
              <option value="">{d.clear}</option>
              {detection.history.map((record, index) => (
                <option key={record.id} value={record.id}>
                  {historyLabel(index)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className={styles.iconButton}
              aria-label={d.historyDelete}
              title={d.historyDelete}
              disabled={detection.selectedId === null}
              onClick={() => detection.selectedId && detection.remove(detection.selectedId)}
            >
              <DeleteIcon />
            </button>
          </div>
        </div>
      )}

      {detection.selectedId !== null && detection.candidates.length > 0 && (
        <>
          <p className={styles.mutedText}>{fmt(d.candidates, { count: detection.candidates.length })}</p>
          <p className={styles.hint}>{d.tallNote}</p>
        </>
      )}
      {detection.emptyResult && <p className={styles.mutedText}>{d.none}</p>}
      {detection.historyError && <p className={styles.errorText}>{d.historyLoadFailed}</p>}
      {detection.error && <p className={styles.errorText}>{detection.error}</p>}
    </section>
  )
}

interface RegionCardProps {
  ref: (el: HTMLLIElement | null) => void
  region: EditorRegion
  index: number
  total: number
  selected: boolean
  referenceDetail: AssetDetail | undefined
  onSelect: () => void
  onKind: (kind: RegionKind) => void
  onInstruction: (value: string) => void
  onText: (value: string) => void
  onPickReference: () => void
  onClearReference: () => void
  onMove: (direction: 'up' | 'down') => void
  onRemove: () => void
}

function RegionCard({
  ref,
  region,
  index,
  total,
  selected,
  referenceDetail,
  onSelect,
  onKind,
  onInstruction,
  onText,
  onPickReference,
  onClearReference,
  onMove,
  onRemove,
}: RegionCardProps) {
  const { t } = useI18n()
  const c = t.regions.card
  const id = useId()
  const problems = regionProblems(region)
  const placeholder = region.hint ? fmt(c.hintPlaceholder, { label: region.hint }) : c.instructionPlaceholder[region.kind]
  return (
    <li
      ref={ref}
      className={styles.card}
      data-selected={selected}
      data-invalid={problems.length > 0 || undefined}
      aria-label={fmt(c.aria, { n: index + 1 })}
      onFocusCapture={onSelect}
      onPointerDown={onSelect}
    >
      <div className={styles.cardHeader}>
        <span
          className={styles.cardBadge}
          style={{ background: regionColor(index), color: regionBadgeTextColor(index) }}
          aria-hidden="true"
        >
          {index + 1}
        </span>
        <label className={styles.srOnly} htmlFor={`${id}-kind`}>
          {c.kindLabel}
        </label>
        <select
          id={`${id}-kind`}
          className={styles.select}
          value={region.kind}
          onChange={(e) => onKind(e.target.value as RegionKind)}
        >
          {REGION_KINDS.map((kind) => (
            <option key={kind} value={kind}>
              {t.regions.kinds[kind]}
            </option>
          ))}
        </select>
        <div className={styles.cardButtons}>
          <button type="button" className={styles.iconButton} aria-label={c.moveUp} title={c.moveUp} disabled={index === 0} onClick={() => onMove('up')}>
            <ArrowUpIcon />
          </button>
          <button
            type="button"
            className={styles.iconButton}
            aria-label={c.moveDown}
            title={c.moveDown}
            disabled={index === total - 1}
            onClick={() => onMove('down')}
          >
            <ArrowDownIcon />
          </button>
          <button type="button" className={styles.iconButton} aria-label={c.remove} title={c.remove} onClick={onRemove}>
            <DeleteIcon />
          </button>
        </div>
      </div>
      {region.kind === 'move' && <p className={styles.hint}>{c.moveHint}</p>}
      <label className={styles.fieldLabel} htmlFor={`${id}-instruction`}>
        {c.instructionLabel}
      </label>
      <textarea
        id={`${id}-instruction`}
        className={styles.textarea}
        rows={2}
        value={region.instruction}
        maxLength={REGION_INSTRUCTION_MAX_LENGTH}
        placeholder={placeholder}
        onChange={(e) => onInstruction(e.target.value)}
      />
      <label className={styles.fieldLabel} htmlFor={`${id}-text`}>
        {c.textLabel}
      </label>
      <input
        id={`${id}-text`}
        type="text"
        className={styles.textInput}
        value={region.text}
        maxLength={REGION_TEXT_MAX_LENGTH}
        placeholder={c.textPlaceholder}
        onChange={(e) => onText(e.target.value)}
      />
      <div className={styles.referenceRow}>
        <span className={styles.fieldLabel}>{c.referenceLabel}</span>
        {region.referenceAssetId ? (
          <span className={styles.referenceThumb}>
            <AssetThumb
              assetId={region.referenceAssetId}
              rating={referenceDetail ? (referenceDetail.content_rating ?? null) : undefined}
              withheld={referenceDetail?.content_withheld === true}
              className={styles.refThumbImage}
              alt=""
              draggable={false}
            />
          </span>
        ) : (
          <span className={styles.mutedText}>{c.referenceNone}</span>
        )}
        <button type="button" className={styles.smallButton} onClick={onPickReference}>
          {c.referencePick}
        </button>
        {region.referenceAssetId && (
          <button type="button" className={styles.smallButton} onClick={onClearReference}>
            {c.referenceRemove}
          </button>
        )}
      </div>
      {problems.length > 0 && (
        <ul className={styles.problemList}>
          {problems.map((p) => (
            <li key={p}>{problemText(t, p)}</li>
          ))}
        </ul>
      )}
    </li>
  )
}

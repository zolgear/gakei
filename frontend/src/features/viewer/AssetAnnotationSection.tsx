/**
 * ビューアの「タイトルとタグ」節(ADR-0024 5章)。グループ欄(`AssetGroupsSection`)と同じ枠で、
 * タイトル(表示、インライン編集、消去)、タグ(チップ、削除、候補付きの追加)、推定の状態、
 * 「再推定」を置く。マスクでは出さない(呼び出し側が `supportsAnnotation` で判断する)。
 *
 * 自動で付いたものと人が付けたものは、文字のラベルではなく見た目で分ける(ADR-0024 2章):
 * 自動のタイトルは控えめな色と点線の下線、タグは `TagChip` の点線の枠。
 *
 * 推定中(queued / running)は Viewer 側の Asset 詳細のクエリが一定間隔で取り直す
 * (`annotationPollInterval`)。終わったら一覧(ストックのタイトル、タグの候補と件数、履歴の
 * 出力のタイトル)を取り直す。削除済みの Asset は編集できない(サーバーが 409)ので表示だけにする。
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  addAssetTag,
  annotateAsset,
  getAnnotationSettings,
  removeAssetTag,
  updateAssetTitle,
  type AssetAnnotationResponse,
  type AssetDetail,
} from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { formatDateTime } from '../../lib/format'
import { useResourcePanel } from '../../context/useResourcePanel'
import { ANNOTATION_SETTINGS_QUERY_KEY } from '../settings/queryKeys'
import { setStockTagFilter } from '../stock/stockTagFilterStore'
import { TagAutocomplete } from '../annotations/TagAutocomplete'
import { TagChip } from '../annotations/TagChip'
import {
  annotationJustFinished,
  canEditAnnotation,
  canRequestAnnotation,
  isAnnotationPending,
  mergeAnnotation,
} from '../annotations/annotationStatus'
import { TAG_NAME_MAX, TITLE_MAX, checkTagInput, isTitleTooLong, normalizeTitleInput } from '../annotations/tagInput'
import styles from './AssetAnnotationSection.module.css'

interface AssetAnnotationSectionProps {
  asset: AssetDetail
}

export function AssetAnnotationSection({ asset }: AssetAnnotationSectionProps) {
  const { t } = useI18n()
  const a = t.viewer.annotation
  const queryClient = useQueryClient()
  const { openPanel } = useResourcePanel()
  const editable = canEditAnnotation(asset)
  const tags = asset.tags ?? []
  const annotation = asset.annotation ?? null
  const pending = isAnnotationPending(annotation)

  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const titleInputRef = useRef<HTMLInputElement | null>(null)

  const settingsQuery = useQuery({
    queryKey: ANNOTATION_SETTINGS_QUERY_KEY,
    queryFn: getAnnotationSettings,
    staleTime: 60_000,
  })
  const showAnnotate = canRequestAnnotation(asset, settingsQuery.data?.usable_engines)

  // 推定が終わったら、一覧側のタイトル・タグを取り直す(詳細は Viewer のポーリングで最新になっている)。
  const previousStatus = useRef(annotation?.status ?? null)
  useEffect(() => {
    const next = annotation?.status ?? null
    if (annotationJustFinished(previousStatus.current, next)) {
      void queryClient.invalidateQueries({ queryKey: ['assets'] })
      void queryClient.invalidateQueries({ queryKey: ['tags'] })
      void queryClient.invalidateQueries({ queryKey: ['runs'] })
    }
    previousStatus.current = next
  }, [annotation?.status, queryClient])

  // 別の Asset に切り替わったら、タイトルの編集をやめる(レンダー中に前回値と比べてリセットする)。
  const [editingAssetId, setEditingAssetId] = useState(asset.id)
  if (editingAssetId !== asset.id) {
    setEditingAssetId(asset.id)
    setEditingTitle(false)
    setError(null)
  }

  function applyResponse(response: AssetAnnotationResponse, options: { tagsChanged?: boolean } = {}) {
    queryClient.setQueryData<AssetDetail>(['asset', response.asset_id], (prev) =>
      prev ? mergeAnnotation(prev, response) : prev,
    )
    void queryClient.invalidateQueries({ queryKey: ['assets'] })
    void queryClient.invalidateQueries({ queryKey: ['runs'] })
    if (options.tagsChanged) void queryClient.invalidateQueries({ queryKey: ['tags'] })
    setError(null)
  }

  function handleError(err: unknown, fallback: string) {
    setError(err instanceof ApiError ? err.message : fallback)
  }

  const titleMutation = useMutation({
    mutationFn: (title: string | null) => updateAssetTitle(asset.id, title),
    onSuccess: (response) => {
      applyResponse(response)
      setEditingTitle(false)
    },
    onError: (err) => handleError(err, a.saveFailed),
  })

  const addTagMutation = useMutation({
    mutationFn: (name: string) => addAssetTag(asset.id, name),
    onSuccess: (response) => applyResponse(response, { tagsChanged: true }),
    onError: (err) => handleError(err, a.saveFailed),
  })

  const removeTagMutation = useMutation({
    mutationFn: (name: string) => removeAssetTag(asset.id, name),
    onSuccess: (response) => applyResponse(response, { tagsChanged: true }),
    onError: (err) => handleError(err, a.saveFailed),
  })

  const annotateMutation = useMutation({
    mutationFn: () => annotateAsset(asset.id),
    onSuccess: (response) => applyResponse(response),
    onError: (err) => handleError(err, a.annotateFailed),
  })

  function startEditTitle() {
    setTitleDraft(asset.title ?? '')
    setError(null)
    setEditingTitle(true)
    // 入力欄が描画されてからフォーカスする。
    requestAnimationFrame(() => {
      titleInputRef.current?.focus()
      titleInputRef.current?.select()
    })
  }

  const titleTooLong = isTitleTooLong(titleDraft)

  function saveTitle() {
    if (titleTooLong) return
    const next = normalizeTitleInput(titleDraft)
    if (next === (asset.title ?? null)) {
      setEditingTitle(false)
      return
    }
    titleMutation.mutate(next)
  }

  function handleAddTag(raw: string): boolean {
    const check = checkTagInput(raw, tags)
    if (!check.ok) {
      setError(
        check.reason === 'empty'
          ? a.tagEmpty
          : check.reason === 'tooLong'
            ? fmt(a.tagTooLong, { max: TAG_NAME_MAX })
            : a.tagDuplicate,
      )
      return false
    }
    addTagMutation.mutate(check.name)
    return true
  }

  function filterStockByTag(name: string) {
    setStockTagFilter(name)
    openPanel('stock')
  }

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>{a.heading}</h3>

      {/* タイトル */}
      {editingTitle ? (
        <div className={styles.titleEdit}>
          <input
            ref={titleInputRef}
            type="text"
            className={styles.titleInput}
            value={titleDraft}
            placeholder={a.titlePlaceholder}
            aria-label={a.titleLabel}
            aria-invalid={titleTooLong}
            disabled={titleMutation.isPending}
            onChange={(e) => setTitleDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing) return
              if (e.key === 'Enter') {
                e.preventDefault()
                saveTitle()
              } else if (e.key === 'Escape') {
                e.preventDefault()
                e.stopPropagation()
                setEditingTitle(false)
              }
            }}
          />
          <div className={styles.buttonRow}>
            <button
              type="button"
              className={styles.primaryButton}
              disabled={titleMutation.isPending || titleTooLong}
              onClick={saveTitle}
            >
              {a.save}
            </button>
            <button
              type="button"
              className={styles.button}
              disabled={titleMutation.isPending}
              onClick={() => setEditingTitle(false)}
            >
              {a.cancel}
            </button>
            {asset.title && (
              <button
                type="button"
                className={`${styles.button} ${styles.dangerButton}`}
                disabled={titleMutation.isPending}
                onClick={() => titleMutation.mutate(null)}
              >
                {a.clearTitle}
              </button>
            )}
          </div>
          {titleTooLong && <p className={styles.error}>{fmt(a.titleTooLong, { max: TITLE_MAX })}</p>}
        </div>
      ) : (
        <div className={styles.titleRow}>
          {asset.title ? (
            <p className={styles.title} data-source={asset.title_source ?? 'user'}>
              {asset.title}
            </p>
          ) : (
            <p className={styles.empty}>{a.noTitle}</p>
          )}
          {editable && (
            <button type="button" className={styles.iconButton} aria-label={a.editTitle} title={a.editTitle} onClick={startEditTitle}>
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M10.5 2.5l3 3L6 13H3v-3l7.5-7.5z"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
        </div>
      )}

      {/* タグ */}
      <div className={styles.tagsBlock}>
        <span className={styles.subLabel}>{a.tagsLabel}</span>
        {tags.length > 0 ? (
          <ul className={styles.tagList}>
            {tags.map((tag) => (
              <li key={tag.name} className={styles.tagItem}>
                <TagChip
                  name={tag.name}
                  source={tag.source}
                  onSelect={() => filterStockByTag(tag.name)}
                  selectLabel={fmt(a.filterByTag, { name: tag.name })}
                  onRemove={editable ? () => removeTagMutation.mutate(tag.name) : undefined}
                  removeLabel={fmt(a.removeTag, { name: tag.name })}
                  removeDisabled={removeTagMutation.isPending}
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.empty}>{a.noTags}</p>
        )}
        {editable && (
          <TagAutocomplete
            placeholder={a.addTagPlaceholder}
            ariaLabel={a.addTagPlaceholder}
            suggestionsLabel={a.suggestions}
            submitLabel={a.addTag}
            exclude={tags}
            disabled={addTagMutation.isPending}
            onPick={handleAddTag}
            onInputChange={() => setError(null)}
          />
        )}
      </div>

      {/* 推定の状態と再推定 */}
      {(annotation || showAnnotate) && (
        <div className={styles.statusRow}>
          {annotation && <AnnotationStatusLine status={annotation.status} error={annotation.error} finishedAt={annotation.finished_at} />}
          {showAnnotate && (
            <button
              type="button"
              className={styles.button}
              title={a.reannotateTooltip}
              disabled={pending || annotateMutation.isPending}
              onClick={() => annotateMutation.mutate()}
            >
              {annotation ? a.reannotate : a.annotate}
            </button>
          )}
        </div>
      )}

      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

function AnnotationStatusLine({
  status,
  error,
  finishedAt,
}: {
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  error?: string | null
  finishedAt?: string | null
}) {
  const { t } = useI18n()
  const a = t.viewer.annotation
  if (status === 'queued' || status === 'running') {
    return (
      <p className={styles.status} data-status={status} role="status">
        <span className={styles.spinner} aria-hidden="true" />
        {status === 'queued' ? a.statusQueued : a.statusRunning}
      </p>
    )
  }
  if (status === 'failed') {
    return (
      <p className={styles.status} data-status="failed">
        {error ? fmt(a.statusFailed, { error }) : a.statusFailedNoReason}
      </p>
    )
  }
  return (
    <p className={styles.status} data-status="succeeded">
      {finishedAt ? fmt(a.statusSucceeded, { date: formatDateTime(finishedAt) }) : a.statusSucceededNoDate}
    </p>
  )
}

/**
 * ビューアの「タイトル」節と「タグ」節(ADR-0024 5章)。グループ欄(`AssetGroupsSection`)と同じ枠。
 * タグが多いと下の情報(大きさ、プロンプト、ダウンロードなど)が押し下げられるので2つに分け、
 * タイトルは情報欄の上部に、タグはプロンプトの下に置く(置き場所は Viewer が決める)。
 * - `AssetTitleSection`: タイトル(表示、インライン編集、消去)、推定の状態、「再推定」。
 *   推定はタイトルとタグの両方に関わるが、上にあって気づきやすいのでこちらに置く。
 * - `AssetTagsSection`: タグ(チップ、削除、候補付きの追加)。多いときは先頭だけ出して畳む
 *   (`tagCollapseState`)。追加の入力欄は畳んでいても常に出す。
 * マスクでは出さない(呼び出し側が `supportsAnnotation` で判断する)。
 *
 * 自動で付いたものと人が付けたものは、文字のラベルではなく見た目で分ける(ADR-0024 2章):
 * 自動のタイトルは控えめな色と点線の下線、タグは `TagChip` の点線の枠。
 *
 * どちらの節も Viewer が取得した Asset 詳細(`['asset', id]`)を props で受け取り、自分では
 * 取り直さない。更新の応答は共有のフック `useAnnotationEditing` の `applyResponse` でその
 * キャッシュに書き戻す。推定中(queued / running)は Viewer 側のクエリが一定間隔で取り直す
 * (`annotationPollInterval`)。終わったら一覧(ストックのタイトル、タグの候補と件数、履歴の
 * 出力のタイトル)を取り直す(タイトル節が受け持つ)。削除済みの Asset は編集できない
 * (サーバーが 409)ので表示だけにする。
 */
import { useEffect, useId, useRef, useState } from 'react'
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
import { tagCollapseState } from './tagCollapse'
import styles from './AssetAnnotationSection.module.css'

interface AnnotationSectionProps {
  asset: AssetDetail
}

/**
 * タイトル節とタグ節で共有する更新まわり。応答を Asset 詳細のキャッシュに書き戻し、一覧を
 * 取り直させる。エラーの表示は節ごとに持つ(操作した節の近くに出す)。別の Asset に切り替わったら
 * エラーを消す。
 */
function useAnnotationEditing(asset: AssetDetail) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)

  const [errorAssetId, setErrorAssetId] = useState(asset.id)
  if (errorAssetId !== asset.id) {
    setErrorAssetId(asset.id)
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

  return { editable: canEditAnnotation(asset), error, setError, applyResponse, handleError }
}

function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <p className={styles.error} role="alert">
      {error}
    </p>
  )
}

export function AssetTitleSection({ asset }: AnnotationSectionProps) {
  const { t } = useI18n()
  const a = t.viewer.annotation
  const queryClient = useQueryClient()
  const { editable, error, setError, applyResponse, handleError } = useAnnotationEditing(asset)
  const annotation = asset.annotation ?? null
  const pending = isAnnotationPending(annotation)

  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
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
  }

  const titleMutation = useMutation({
    mutationFn: (title: string | null) => updateAssetTitle(asset.id, title),
    onSuccess: (response) => {
      applyResponse(response)
      setEditingTitle(false)
    },
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

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>{a.titleLabel}</h3>

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

      {/* 推定の状態と再推定(タイトルとタグの両方が対象) */}
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

      <ErrorLine error={error} />
    </div>
  )
}

export function AssetTagsSection({ asset }: AnnotationSectionProps) {
  const { t } = useI18n()
  const a = t.viewer.annotation
  const { openPanel } = useResourcePanel()
  const { editable, error, setError, applyResponse, handleError } = useAnnotationEditing(asset)
  const tags = asset.tags ?? []
  const listId = useId()

  // 展開状態は画像ごとに覚えない。別の Asset に切り替わったら畳む。
  const [expanded, setExpanded] = useState(false)
  const [expandedAssetId, setExpandedAssetId] = useState(asset.id)
  if (expandedAssetId !== asset.id) {
    setExpandedAssetId(asset.id)
    setExpanded(false)
  }
  const collapse = tagCollapseState(tags.length, expanded)
  const visibleTags = tags.slice(0, collapse.visibleCount)

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
      <h3 className={styles.heading}>{a.tagsLabel}</h3>

      {tags.length > 0 ? (
        <ul id={listId} className={styles.tagList}>
          {visibleTags.map((tag) => (
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
      {collapse.collapsible && (
        <button
          type="button"
          className={styles.toggleButton}
          aria-expanded={expanded}
          aria-controls={listId}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? a.collapseTags : fmt(a.showAllTags, { count: tags.length })}
        </button>
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

      <ErrorLine error={error} />
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

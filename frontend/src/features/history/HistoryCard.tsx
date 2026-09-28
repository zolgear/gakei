/**
 * 履歴の「ノード風カード」。生成/編集は UI 上で区別しない(ADR-0009)ので GENERATE/EDIT の
 * 大きなラベルは出さない。入力画像があれば「入力 n」を表示し、色点でも入力の有無を示す
 * (水色=入力あり、アクセント色=入力なし)。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router'
import { ApiError, cancelRun, deleteRun, getRun, type RunSummary } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { UserAvatar } from '../../components/UserAvatar'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { useResourcePanel } from '../../context/useResourcePanel'
import { useAddToInputs } from '../run-form/useAddToInputs'
import { AddToInputsDialog } from '../run-form/AddToInputsDialog'
import { paramsForRerun } from '../run-form/paramsBuilder'
import { GAKEI_ASSET_ID_DATA_TYPE } from '../run-form/dragDropAssets'
import { inputsFromRunInputs } from '../run-form/editInputs'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { removeItemFromPagedData, type InfiniteQueryData } from '../../lib/queryCache'
import {
  formatDateTime,
  formatDuration,
  formatElapsedSeconds,
  shortenModelName,
  statusLabel,
} from '../../lib/format'
import { buildParamChips, formatTokenCount } from './historyChips'
import { canDeleteRun } from './runDeletion'
import { buildRunDeleteWarning } from './runDeleteWarning'
import { resolveHistoryCardNavigation } from './historyNavigation'
import { fmt, useI18n } from '../../i18n'
import styles from './HistoryCard.module.css'

interface HistoryCardProps {
  run: RunSummary
}

function RerunIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 8a5 5 0 1 0 1.6-3.7M3 2.2v3.3h3.3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function UseAsInputIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M10.5 2.5l3 3L6 13H3v-3z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 4.5h10M6.5 4.5V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1.5M4.5 4.5v8a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1v-8M6.5 7.5v3.5M9.5 7.5v3.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function LineageIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      <rect x="2" y="2.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <rect x="11" y="7" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <rect x="2" y="11.5" width="5" height="4" rx="1" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M7 4.5c3 0 1.5 4.5 4 4.5M7 13.5c3 0 1.5-4.5 4-4.5"
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </svg>
  )
}

export function HistoryCard({ run }: HistoryCardProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { setFormState } = useRunFormContext()
  const { setOriginAssetId } = useLineageOrigin()
  const { openPanel } = useResourcePanel()
  const addToInputs = useAddToInputs()
  const [rerunError, setRerunError] = useState<string | null>(null)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const outputs = run.outputs ?? []
  const firstOutput = outputs[0]
  const hasInputs = run.input_count > 0
  const accentClass = hasInputs ? styles.dotEdit : styles.dotGenerate
  const paramChips = buildParamChips(run.params)
  const tokenLabel = formatTokenCount(run.usage)
  const parentAssetId = run.primary_parent_asset_id
  const extraInputCount = run.input_count > 1 ? run.input_count - 1 : 0

  // 画像領域は出力があればそのビューアへ、出力が無く実行中・待機中ならスタジオの生成画面へ、
  // それ以外は Run 詳細へ。プロンプト部分は常に Run 詳細へ。「系列を見る」は辿れる起点
  // (出力、無ければ主たる親)があるときだけ出す(失敗した Run にも出せるように)。
  const nav = resolveHistoryCardNavigation({
    runId: run.id,
    status: run.status,
    firstOutputAssetId: firstOutput?.asset_id ?? null,
    primaryParentAssetId: parentAssetId ?? null,
  })
  const isPending = run.status === 'running' || run.status === 'queued'
  const mediaAriaLabel = firstOutput
    ? t.history.card.showImage
    : isPending
      ? t.history.card.watchProgress
      : t.history.card.viewDetail

  const cancelMutation = useMutation({
    mutationFn: () => cancelRun(run.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })

  const rerunMutation = useMutation({
    mutationFn: () => getRun(run.id),
    onSuccess: (detail) => {
      setRerunError(null)
      // comfyui_*(ADR-0013)はサーバーが 422 で拒否するので、フォームへ戻す前に取り除く。
      // 実際に使った seed(comfyui_seed)があれば seed として固定する(同じ画像を再現できる
      // ように。ランダムに戻すのは seed の専用欄のトグル1つ)。
      setFormState({
        provider: detail.provider,
        model: detail.model,
        prompt: detail.prompt,
        params: paramsForRerun((detail.params ?? {}) as Record<string, unknown>) as Record<
          string,
          string | number | boolean
        >,
        inputs: inputsFromRunInputs(detail.inputs ?? []),
        // 削除済みのグループなら null(=「なし」)で返ってくる。
        assetGroupId: detail.asset_group?.id ?? null,
      })
      navigate('/studio')
    },
    onError: (err: unknown) => {
      setRerunError(err instanceof ApiError ? err.message : t.history.card.rerunFailed)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => deleteRun(run.id),
    onSuccess: () => {
      setDeleteConfirmOpen(false)
      setDeleteError(null)
      queryClient.setQueryData<InfiniteQueryData<RunSummary>>(['runs'], (old) =>
        removeItemFromPagedData(old, run.id),
      )
      // 出力 Asset も削除されたので、ストック等のキャッシュも整合させる。
      queryClient.invalidateQueries({ queryKey: ['assets'] })
    },
    onError: (err: unknown) => {
      setDeleteConfirmOpen(false)
      setDeleteError(err instanceof ApiError ? err.message : t.history.card.deleteFailed)
    },
  })

  function handleShowLineage() {
    const originAssetId = nav.lineageOriginAssetId
    if (!originAssetId) return
    setOriginAssetId(originAssetId)
    // モバイルはドロワー内の小さいグラフだと実用的でないので、大きい表示へ直接遷移する。
    const isMobile = typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
    if (isMobile) {
      navigate(`/lineage/${originAssetId}`)
    } else {
      openPanel('graph')
    }
  }

  const elapsed = run.status === 'running' ? formatElapsedSeconds(run.started_at) : null

  const parentOverlay = parentAssetId && (
    <div className={styles.parentOverlay}>
      <img
        className={styles.parentThumb}
        src={assetUrl(parentAssetId, 'thumb')}
        alt={t.history.card.parent}
        draggable={false}
      />
      <span>
        {extraInputCount > 0 ? fmt(t.history.card.parentWithExtra, { extra: extraInputCount }) : t.history.card.parent}
      </span>
    </div>
  )

  return (
    <article className={styles.card} data-highlight={run.status === 'running'}>
      <div className={styles.header}>
        <span className={`${styles.dot} ${accentClass}`} aria-hidden="true" />
        {hasInputs && (
          <span className={styles.inputBadge}>{fmt(t.history.card.inputBadge, { count: run.input_count })}</span>
        )}
        <span className={styles.model}>{run.model_label ?? shortenModelName(run.model)}</span>
        <span className={styles.status} data-status={run.status}>
          {statusLabel(run.status)}
        </span>
      </div>

      <div className={styles.media}>
        <Link
          to={nav.mediaHref}
          className={styles.mediaLink}
          aria-label={mediaAriaLabel}
        >
          {run.status === 'succeeded' && firstOutput && (
            <>
              <img
                className={`${styles.mediaImage} checkerboard`}
                src={assetUrl(firstOutput.asset_id, 'thumb')}
                alt=""
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, firstOutput.asset_id)
                  e.dataTransfer.effectAllowed = 'copy'
                }}
              />
              {outputs.length > 1 && (
                <span className={styles.countBadge}>{fmt(t.history.card.outputCount, { count: outputs.length })}</span>
              )}
            </>
          )}

          {run.status === 'failed' && (
            <div className={styles.errorArea}>
              <span className={styles.errorCode}>{run.error_code ?? 'error'}</span>
              {run.error_message && <span className={styles.errorMessage}>{run.error_message}</span>}
            </div>
          )}

          {(run.status === 'running' || run.status === 'queued') && (
            <div className={styles.pendingArea}>
              <span className={styles.pendingText}>
                {run.status === 'running' ? (elapsed ?? t.history.card.running) : t.history.card.queued}
              </span>
              {run.status === 'running' && (
                <div className={styles.progressTrack}>
                  <div className={styles.progressBar} />
                </div>
              )}
            </div>
          )}

          {run.status === 'canceled' && (
            <div className={styles.pendingArea}>
              <span className={styles.pendingText}>{t.history.card.canceled}</span>
            </div>
          )}
        </Link>

        {parentOverlay}
      </div>

      <div className={styles.body}>
        <Link
          to={nav.runHref}
          className={styles.promptLink}
          aria-label={
            run.status === 'queued' || run.status === 'running'
              ? t.history.card.watchProgress
              : t.history.card.viewDetail
          }
        >
          <p className={styles.prompt}>{run.prompt || t.history.card.noPrompt}</p>
        </Link>
        <div className={styles.meta}>
          {formatDateTime(run.queued_at)}
          {run.finished_at && ` · ${formatDuration(run.started_at, run.finished_at)}`}
          {!run.finished_at && elapsed && ` · ${elapsed}`}
          {tokenLabel && ` · ${tokenLabel}`}
          {run.created_by && (
            <span className={styles.byRow}>
              {' · '}
              {run.created_by.avatar_url && (
                <UserAvatar
                  name={run.created_by.name ?? run.created_by.email}
                  avatarUrl={run.created_by.avatar_url}
                  size={16}
                />
              )}
              {fmt(t.history.card.by, { name: run.created_by.name ?? run.created_by.email ?? '-' })}
            </span>
          )}
        </div>

        {paramChips.length > 0 && (
          <div className={styles.chipRow}>
            {paramChips.map((chip) => (
              <span key={chip} className={styles.chip}>
                {chip}
              </span>
            ))}
          </div>
        )}

        <div className={styles.actions}>
          {run.status === 'queued' && (
            <button
              type="button"
              className={styles.textButton}
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
            >
              {t.history.card.cancel}
            </button>
          )}
          <span className={styles.actionsSpacer} />
          {nav.showLineageButton && (
            <button
              type="button"
              className={styles.iconButton}
              aria-label={t.history.card.showLineage}
              onClick={handleShowLineage}
            >
              <LineageIcon />
            </button>
          )}
          {run.status !== 'queued' && run.status !== 'running' && (
            <button
              type="button"
              className={styles.iconButton}
              aria-label={t.history.card.rerun}
              onClick={() => rerunMutation.mutate()}
              disabled={rerunMutation.isPending}
            >
              <RerunIcon />
            </button>
          )}
          {run.status === 'succeeded' && firstOutput && (
            <button
              type="button"
              className={`${styles.iconButton} ${styles.iconButtonEdit}`}
              aria-label={t.history.card.useAsInput}
              onClick={() => addToInputs.request(firstOutput.asset_id)}
            >
              <UseAsInputIcon />
            </button>
          )}
          {canDeleteRun(run.status) && (
            <button
              type="button"
              className={`${styles.iconButton} ${styles.iconButtonDanger}`}
              aria-label={t.history.card.delete}
              onClick={() => setDeleteConfirmOpen(true)}
              disabled={deleteMutation.isPending}
            >
              <TrashIcon />
            </button>
          )}
        </div>
        {rerunError && <p className={styles.errorText}>{rerunError}</p>}
        {deleteError && <p className={styles.errorText}>{deleteError}</p>}
      </div>

      <AddToInputsDialog
        open={addToInputs.isPending}
        onAdd={() => addToInputs.resolve('add')}
        onReplace={() => addToInputs.resolve('replace')}
        onCancel={addToInputs.cancel}
      />
      <ConfirmDialog
        open={deleteConfirmOpen}
        message={fmt(t.history.card.deleteConfirm.message, { count: outputs.length })}
        warning={buildRunDeleteWarning(run.descendant_run_count)}
        previewImageUrl={firstOutput ? assetUrl(firstOutput.asset_id, 'thumb') : undefined}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </article>
  )
}

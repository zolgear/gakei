/**
 * 履歴のカードの操作(詳細・系列・削除、待機中はキャンセル)と、削除の確認のダイアログ。詳細の形
 * (重ねるカード、検索ページの従来のカード)と画像の形(ギャラリーのタイル)で同じロジックを使う。
 * 再実行と「入力に使う」は置かない(Generated 詳細とビューアにある)。狭いカードでも1行に収める。
 *
 * 操作の並び(`actions`)、失敗の文(`errors`)、ダイアログ(`dialogs`)を別々に返す。ダイアログは
 * 画面に固定で出すので、ホバーのときだけ見える重ね表示の中ではなく、カードの直下に置く。
 */
import { useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router'
import { ApiError, cancelRun, deleteRun, type RunOutputRef, type RunSummary } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { RunDetailIcon } from '../../components/icons'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { useResourcePanel } from '../../context/useResourcePanel'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { removeItemFromPagedData, type InfiniteQueryData } from '../../lib/queryCache'
import { canDeleteRun } from './runDeletion'
import { buildRunDeleteWarning } from './runDeleteWarning'
import type { HistoryCardNavigation } from './historyNavigation'
import { fmt, useI18n } from '../../i18n'
import { LineageIcon, TrashIcon } from './historyActionIcons'
import styles from './HistoryCard.module.css'

interface HistoryRunActions {
  actions: ReactNode
  errors: ReactNode
  dialogs: ReactNode
}

export interface HistoryRunActionsOptions {
  /** 操作の行の左端に置くもの(ギャラリーのピクセルの大きさ)。 */
  leading?: ReactNode
}

export function useHistoryRunActions(
  run: RunSummary,
  shownOutput: RunOutputRef | undefined,
  nav: HistoryCardNavigation,
  { leading }: HistoryRunActionsOptions = {},
): HistoryRunActions {
  const { t } = useI18n()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { setOriginAssetId } = useLineageOrigin()
  const { openPanel } = useResourcePanel()
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const outputs = run.outputs ?? []

  const cancelMutation = useMutation({
    mutationFn: () => cancelRun(run.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
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
      // 画像単位の履歴(グループ・似た順。ADR-0054)からも外す。
      queryClient.invalidateQueries({ queryKey: ['history-assets'] })
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

  const actions = (
    <div className={styles.actions}>
      {leading}
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
      {/* アイコンだけのボタンには、どれも aria-label と同じ文の title を付けてツールチップを揃える。 */}
      {/* プロンプトの文字のリンクと同じ遷移先(Run 詳細)を、アイコンからも開けるようにする。
          状態によらず出す(失敗・中止・実行中の Run にも詳細はある)。 */}
      <Link
        to={nav.runHref}
        className={styles.iconButton}
        aria-label={t.common.openGeneratedDetail}
        title={t.common.openGeneratedDetail}
      >
        <RunDetailIcon size={15} />
      </Link>
      {nav.showLineageButton && (
        <button
          type="button"
          className={styles.iconButton}
          aria-label={t.history.card.showLineage}
          title={t.history.card.showLineage}
          onClick={handleShowLineage}
        >
          <LineageIcon />
        </button>
      )}
      {canDeleteRun(run.status) && (
        <button
          type="button"
          className={`${styles.iconButton} ${styles.iconButtonDanger}`}
          aria-label={t.history.card.delete}
          title={t.history.card.delete}
          onClick={() => setDeleteConfirmOpen(true)}
          disabled={deleteMutation.isPending}
        >
          <TrashIcon />
        </button>
      )}
    </div>
  )

  const errors = (
    <>
      {deleteError && <p className={styles.errorText}>{deleteError}</p>}
    </>
  )

  const dialogs = (
    <>
      <ConfirmDialog
        open={deleteConfirmOpen}
        message={fmt(t.history.card.deleteConfirm.message, { count: outputs.length })}
        warning={buildRunDeleteWarning(run.descendant_run_count)}
        previewImageUrl={shownOutput ? assetUrl(shownOutput.asset_id, 'thumb') : undefined}
        previewWithheld={shownOutput?.content_withheld}
        previewFocalPoint={shownOutput?.focal_point}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </>
  )

  return { actions, errors, dialogs }
}

/**
 * 履歴のカードの操作(詳細・系列・再実行・入力に使う・削除、待機中はキャンセル)と、その確認の
 * ダイアログ。詳細の形(重ねるカード、検索ページの従来のカード)と画像の形(ギャラリーのタイル)で
 * 同じロジックを使う。
 *
 * 操作の並び(`actions`)、失敗の文(`errors`)、ダイアログ(`dialogs`)を別々に返す。ダイアログは
 * 画面に固定で出すので、ホバーのときだけ見える重ね表示の中ではなく、カードの直下に置く。
 */
import { useState, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router'
import { ApiError, cancelRun, deleteRun, getRun, type RunOutputRef, type RunSummary } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { RunDetailIcon } from '../../components/icons'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { useResourcePanel } from '../../context/useResourcePanel'
import { useAddToInputs } from '../run-form/useAddToInputs'
import { AddToInputsDialog } from '../run-form/AddToInputsDialog'
import { paramsForRerun } from '../run-form/paramsBuilder'
import { inputsFromRunInputs } from '../run-form/editInputs'
import { restoreRunRegions } from '../regions/regions'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { removeItemFromPagedData, type InfiniteQueryData } from '../../lib/queryCache'
import { canDeleteRun } from './runDeletion'
import { buildRunDeleteWarning } from './runDeleteWarning'
import type { HistoryCardNavigation } from './historyNavigation'
import { fmt, useI18n } from '../../i18n'
import { LineageIcon, RerunIcon, TrashIcon, UseAsInputIcon } from './historyActionIcons'
import styles from './HistoryCard.module.css'

interface HistoryRunActions {
  actions: ReactNode
  errors: ReactNode
  dialogs: ReactNode
}

export function useHistoryRunActions(
  run: RunSummary,
  shownOutput: RunOutputRef | undefined,
  nav: HistoryCardNavigation,
): HistoryRunActions {
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
      // 領域の指示(ADR-0053 4章)。四角を戻し、サーバーが足した入力とプロンプトの末尾の文を除く。
      const restored = restoreRunRegions({ prompt: detail.prompt, inputs: detail.inputs ?? [], regions: detail.regions })
      setFormState({
        provider: detail.provider,
        model: detail.model,
        prompt: restored.prompt,
        params: paramsForRerun((detail.params ?? {}) as Record<string, unknown>) as Record<
          string,
          string | number | boolean
        >,
        inputs: inputsFromRunInputs(restored.inputs),
        // 削除済みのグループなら null(=「なし」)で返ってくる。
        assetGroupId: detail.asset_group?.id ?? null,
        regions: restored.regions,
        regionsAssetId: restored.regionsAssetId,
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

  const actions = (
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
      {run.status === 'succeeded' && shownOutput && (
        <button
          type="button"
          className={`${styles.iconButton} ${styles.iconButtonEdit}`}
          aria-label={t.history.card.useAsInput}
          onClick={() => addToInputs.request(shownOutput.asset_id)}
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
  )

  const errors = (
    <>
      {rerunError && <p className={styles.errorText}>{rerunError}</p>}
      {deleteError && <p className={styles.errorText}>{deleteError}</p>}
    </>
  )

  const dialogs = (
    <>
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

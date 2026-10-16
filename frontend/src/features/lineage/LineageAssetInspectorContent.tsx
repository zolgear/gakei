/**
 * 系列グラフのインスペクター(`/lineage/:assetId` 専用)で Asset ノードを選んだときの中身。
 * プレビュー画像 + Asset の詳細(`AssetDetailSections`。ビューアの情報欄と同じ)+ 操作。
 * 削除は置かない。系列グラフの途中を消す操作は、来歴を残すこのアプリの主旨に反する
 * (削除は履歴・ストック・ビューアから行う)。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { ApiError, getAsset, restoreAsset } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { useAddToInputs } from '../run-form/useAddToInputs'
import { AddToInputsDialog } from '../run-form/AddToInputsDialog'
import { formatDateTime } from '../../lib/format'
import { shouldShowNotRestorableNote, shouldShowRestoreButton } from '../../lib/assetRestore'
import { fmt, useI18n } from '../../i18n'
import { ExportLineageDialog } from '../lineage-transfer/ExportLineageDialog'
import { AssetThumb } from '../content-rating/AssetThumb'
import { contentRatingPollInterval, thumbRating } from '../content-rating/contentRating'
import { annotationPollInterval } from '../annotations/annotationStatus'
import { AssetDetailSections } from '../viewer/AssetDetailSections'
import { isSendBlocked, isWithheld } from '../mods/modVerdicts'
import styles from './LineageAssetInspectorContent.module.css'

export interface LineageAssetInspectorContentProps {
  assetId: string
  /**
   * 生成元の Run を開く処理。見出しの「i」のアイコンと同じ動きにする(同じグラフにその Run の
   * ノードがあればインスペクターを切り替える)。省略時は Run 詳細ページ(`/runs/:id`)へのリンク。
   */
  onOpenRun?: (runId: string) => void
}

export function LineageAssetInspectorContent({ assetId, onOpenRun }: LineageAssetInspectorContentProps) {
  const { t } = useI18n()
  // 推定中(ADR-0024)とレーティングの判定中(ADR-0049)は、ビューアと同じく終わるまで取り直す。
  const assetQuery = useQuery({
    queryKey: ['asset', assetId],
    queryFn: () => getAsset(assetId),
    refetchInterval: (query) =>
      annotationPollInterval(query.state.data) || contentRatingPollInterval(query.state.data),
  })
  const addToInputs = useAddToInputs()
  const queryClient = useQueryClient()
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [exportOpen, setExportOpen] = useState(false)

  const restoreMutation = useMutation({
    mutationFn: () => restoreAsset(assetId),
    onSuccess: () => {
      setRestoreError(null)
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset', assetId] })
    },
    onError: (err: unknown) => {
      setRestoreError(err instanceof ApiError ? err.message : t.lineage.restoreFailed)
    },
  })

  const asset = assetQuery.data
  const isReady = !assetQuery.isLoading && !assetQuery.isError && asset !== undefined

  if (assetQuery.isLoading) return <p className={styles.placeholder}>{t.lineage.loading}</p>
  if (assetQuery.isError || !isReady) return <p className={styles.placeholder}>{t.lineage.assetLoadError}</p>

  return (
    <div className={styles.content}>
      {asset.deleted_at && (
        <div className={styles.deletedBanner}>
          <p className={styles.deletedBannerText}>{fmt(t.lineage.deletedAt, { date: formatDateTime(asset.deleted_at) })}</p>
          {shouldShowRestoreButton(asset.deleted_at, asset.restorable) && (
            <button
              type="button"
              className={styles.restoreButton}
              onClick={() => restoreMutation.mutate()}
              disabled={restoreMutation.isPending}
            >
              {t.lineage.restoreToStock}
            </button>
          )}
          {shouldShowNotRestorableNote(asset.deleted_at, asset.restorable) && (
            <p className={styles.notRestorableNote}>{t.lineage.notRestorableNote}</p>
          )}
          {restoreError && <p className={styles.deleteErrorText}>{restoreError}</p>}
        </div>
      )}

      <div className={styles.previewFrame}>
        <AssetThumb
          assetId={asset.id}
          rating={thumbRating(asset)}
          badge
          withheld={isWithheld(asset)}
          withheldLabel={asset.content_withheld_verdict?.label}
          src={assetUrl(asset.id, 'preview')}
          className={`${styles.preview} checkerboard`}
          alt=""
          draggable={false}
        />
      </div>

      {/* タイトル、寸法など、生成元、タグ、レーティングなど(ビューアの情報欄と共通)。 */}
      <AssetDetailSections asset={asset} onOpenRun={onOpenRun} />

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.actionButton}
          onClick={() => addToInputs.request(asset.id)}
          disabled={Boolean(asset.deleted_at) || isSendBlocked(asset)}
          title={isSendBlocked(asset) ? t.mods.cannotUseAsInput : undefined}
        >
          {t.lineage.useAsInput}
        </button>
        <Link to={`/assets/${asset.id}`} className={styles.actionButton}>
          {t.lineage.openInViewer}
        </Link>
        {/* 画像を残さなかった Asset(ADR-0051)はダウンロードできないので出さない。 */}
        {!isWithheld(asset) && (
          <a className={styles.actionButton} href={assetUrl(asset.id, 'original', { download: true })}>
            {t.lineage.download}
          </a>
        )}
        {/* 系列を ZIP に書き出す(ADR-0037)。 */}
        {!asset.deleted_at && (
          <button type="button" className={styles.actionButton} onClick={() => setExportOpen(true)}>
            {t.lineage.exportLineage}
          </button>
        )}
      </div>

      <ExportLineageDialog open={exportOpen} assetId={asset.id} onClose={() => setExportOpen(false)} />

      <AddToInputsDialog
        open={addToInputs.isPending}
        onAdd={() => addToInputs.resolve('add')}
        onReplace={() => addToInputs.resolve('replace')}
        onCancel={addToInputs.cancel}
      />
    </div>
  )
}

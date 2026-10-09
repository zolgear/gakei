/**
 * 系列グラフのインスペクター(`/lineage/:assetId` 専用)で Asset ノードを選んだときの中身。
 * ビューアの縮小版(プレビュー画像 + メタデータ + 生成元へのリンク + 操作)。
 * 削除は置かない。系列グラフの途中を消す操作は、来歴を残すこのアプリの主旨に反する
 * (削除は履歴・ストック・ビューアから行う)。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { ApiError, getAsset, getRun, restoreAsset } from '../../api/client'
import { runSeedDisplay } from '../run-detail/runSeedDisplay'
import { assetUrl } from '../../api/assetUrl'
import { useAddToInputs } from '../run-form/useAddToInputs'
import { AddToInputsDialog } from '../run-form/AddToInputsDialog'
import { formatBytes, formatDateTime } from '../../lib/format'
import { shouldShowNotRestorableNote, shouldShowRestoreButton } from '../../lib/assetRestore'
import { fmt, useI18n } from '../../i18n'
import { EmbeddedMetaSection } from './EmbeddedMetaSection'
import { OriginRecipeSection } from './OriginRecipeSection'
import { PromptTagsActions } from '../prompt-tags/PromptTagsActions'
import { FinalPromptSection } from '../run-detail/FinalPromptSection'
import { baselineNegativePrompt } from '../run-detail/finalPrompt'
import { StudioPromptActions } from '../workspace/StudioPromptActions'
import { ExportLineageDialog } from '../lineage-transfer/ExportLineageDialog'
import styles from './LineageAssetInspectorContent.module.css'

export interface LineageAssetInspectorContentProps {
  assetId: string
}

export function LineageAssetInspectorContent({ assetId }: LineageAssetInspectorContentProps) {
  const { t } = useI18n()
  const assetQuery = useQuery({ queryKey: ['asset', assetId], queryFn: () => getAsset(assetId) })
  const runId = assetQuery.data?.produced_by_run?.id
  // queryKey はビューアの生成元 Run と共有する(同じ Run を二重に取得しない)。
  const runQuery = useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId as string),
    enabled: runId !== undefined,
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
  // ComfyUI / SD WebUI の Run だけ、実際に使った seed を出す(runSeedDisplay.ts)。
  const seed = runQuery.data ? runSeedDisplay(runQuery.data, asset?.output_index) : null

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

      <img
        className={`${styles.preview} checkerboard`}
        src={assetUrl(asset.id, 'preview')}
        alt=""
        draggable={false}
      />

      <dl className={styles.metaList}>
        <dt>{t.lineage.dimensions}</dt>
        <dd>
          {asset.width} × {asset.height} px
        </dd>
        <dt>{t.lineage.format}</dt>
        <dd>{asset.mime}</dd>
        <dt>{t.lineage.size}</dt>
        <dd>{formatBytes(asset.bytes)}</dd>
        <dt>{t.lineage.created}</dt>
        <dd>{formatDateTime(asset.created_at)}</dd>
      </dl>

      {asset.produced_by_run && (
        <>
          <h3 className={styles.subheading}>{t.lineage.promptHeading}</h3>
          <p className={styles.runPrompt}>{asset.produced_by_run.prompt.slice(0, 80)}</p>
          {seed && (
            <p className={styles.runMeta} title={seed.tooltip}>
              {seed.text}
            </p>
          )}
          <Link to={`/runs/${asset.produced_by_run.id}`} className={styles.runLink}>
            {t.lineage.viewRunDetail}
          </Link>
          {/* 最終プロンプト(ADR-0030)と、この画像の展開後のプロンプト(ADR-0038 7章)。ビューアと同じ。 */}
          <FinalPromptSection
            className={styles.finalPrompt}
            textOutputs={asset.produced_by_run.text_outputs}
            outputIndex={asset.output_index ?? null}
            prompt={asset.produced_by_run.prompt}
            negativePrompt={runQuery.data ? baselineNegativePrompt(runQuery.data.params) : undefined}
            headingClassName={styles.subheading}
            renderActions={(text) => <StudioPromptActions prompt={text} />}
          />
        </>
      )}

      {asset.origin && <OriginRecipeSection origin={asset.origin} />}
      {asset.embedded_meta && <EmbeddedMetaSection meta={asset.embedded_meta} />}

      {/* ADR-0039 1章: タグをプロンプトに使う(使えるタグが無ければ出さない)。 */}
      <PromptTagsActions assetId={asset.id} />

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.actionButton}
          onClick={() => addToInputs.request(asset.id)}
          disabled={Boolean(asset.deleted_at)}
        >
          {t.lineage.useAsInput}
        </button>
        <Link to={`/assets/${asset.id}`} className={styles.actionButton}>
          {t.lineage.openInViewer}
        </Link>
        <a className={styles.actionButton} href={assetUrl(asset.id, 'original', { download: true })}>
          {t.lineage.download}
        </a>
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

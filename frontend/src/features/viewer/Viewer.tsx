/**
 * 4K ビューア。パン/ズーム表示は `AssetCanvas`(スタジオの結果プレビューと共用)に委ねる。
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router'
import { ApiError, deleteAsset, getAsset, getRun, getShareSettings, restoreAsset } from '../../api/client'
import {
  comfyUiSeedTooltip,
  describeComfyUiSeed,
  extractComfyUiSeed,
} from '../run-detail/comfyuiPromptDisplay'
import { assetUrl } from '../../api/assetUrl'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { useResourcePanel } from '../../context/useResourcePanel'
import { useBackNavigate } from '../../lib/useBackNavigate'
import { isMobileViewport } from '../../lib/viewport'
import { useAddToInputs } from '../run-form/useAddToInputs'
import { AddToInputsDialog } from '../run-form/AddToInputsDialog'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ToastHost, useToast } from '../../components/Toast'
import { formatBytes, formatDateTime } from '../../lib/format'
import { shouldShowNotRestorableNote, shouldShowRestoreButton } from '../../lib/assetRestore'
import { fmt, useI18n } from '../../i18n'
import { EmbeddedMetaSection } from '../lineage/EmbeddedMetaSection'
import { OriginRecipeSection } from '../lineage/OriginRecipeSection'
import { AssetGroupsSection } from './AssetGroupsSection'
import { AssetTagsSection, AssetTitleSection } from './AssetAnnotationSection'
import { annotationPollInterval, supportsAnnotation } from '../annotations/annotationStatus'
import { resolveRunOutputNav } from './runOutputs'
import { AssetCanvas } from './AssetCanvas'
import { ShareDialog } from '../share/ShareDialog'
import { FinalPromptSection } from '../run-detail/FinalPromptSection'
import { StudioPromptActions } from '../workspace/StudioPromptActions'
import { SHARE_SETTINGS_QUERY_KEY } from '../settings/queryKeys'
import styles from './Viewer.module.css'

interface ViewerProps {
  assetId: string
}

/**
 * 生成元 Run の詳細を取得する共通フック。主たる親 Asset(role=image かつ position=0 の入力)と、
 * 出力一覧(他の出力への切り替え用)の両方をこの1回の fetch から導く(queryKey を共有し、
 * 二重に取得しない)。
 */
function useRun(runId: string | undefined) {
  return useQuery({
    queryKey: ['run', runId],
    queryFn: () => getRun(runId as string),
    enabled: runId !== undefined,
  })
}

function isEditableTarget(el: Element | null): boolean {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return (el as HTMLElement).isContentEditable === true
}

export function Viewer({ assetId }: ViewerProps) {
  const { t } = useI18n()
  // 推定中(ADR-0024)は終わるまで取り直す(`annotationPollInterval`)。
  const assetQuery = useQuery({
    queryKey: ['asset', assetId],
    queryFn: () => getAsset(assetId),
    refetchInterval: (query) => annotationPollInterval(query.state.data),
  })
  const { originAssetId, setOriginAssetId } = useLineageOrigin()
  const { selectedPanel, openPanel } = useResourcePanel()
  const goBack = useBackNavigate('/')
  const navigate = useNavigate()
  const addToInputs = useAddToInputs()
  const queryClient = useQueryClient()
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [shareOpen, setShareOpen] = useState(false)
  const toast = useToast()
  // 共有リンク(ADR-0029)は管理者設定で有効なときだけ「共有」を出す。
  const shareSettingsQuery = useQuery({ queryKey: SHARE_SETTINGS_QUERY_KEY, queryFn: getShareSettings })
  const shareEnabled = shareSettingsQuery.data?.enabled === true

  const deleteMutation = useMutation({
    mutationFn: () => deleteAsset(assetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset', assetId] })
      setDeleteConfirmOpen(false)
      // ページ遷移はしない(このページに留まって「削除済みです」バナー・復元ボタンを見せる)。
      toast.show({
        message: t.viewer.deletedToast,
        actionLabel: t.viewer.undo,
        onAction: () => restoreMutation.mutate(),
      })
    },
    onError: (err: unknown) => {
      setDeleteConfirmOpen(false)
      setDeleteError(err instanceof ApiError ? err.message : t.viewer.deleteFailed)
    },
  })

  const restoreMutation = useMutation({
    mutationFn: () => restoreAsset(assetId),
    onSuccess: () => {
      setRestoreError(null)
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset', assetId] })
    },
    onError: (err: unknown) => {
      setRestoreError(err instanceof ApiError ? err.message : t.viewer.restoreFailed)
    },
  })

  // 系列グラフの起点(a): ビューアで開いている Asset を自動的に起点にする。
  useEffect(() => {
    setOriginAssetId(assetId)
  }, [assetId, setOriginAssetId])

  const asset = assetQuery.data
  const runQuery = useRun(asset?.produced_by_run?.id)
  const seedText = runQuery.data
    ? describeComfyUiSeed(
        extractComfyUiSeed((runQuery.data.params ?? {}) as Record<string, unknown>),
        runQuery.data.outputs?.length ?? 0,
        asset?.output_index,
      )
    : null
  const primaryParentAssetId = runQuery.data?.inputs?.find(
    (i) => i.role === 'image' && i.position === 0,
  )?.asset_id
  const outputNav =
    asset && runQuery.data?.outputs ? resolveRunOutputNav(runQuery.data.outputs, asset.id) : null

  // 出力の切り替え: ←/→ キーでも前後の出力へ移動できる。ダイアログ表示中や入力欄にフォーカスが
  // あるとき、AssetCanvas(react-zoom-pan-pinch)がキーボード操作でパン中のときは反応させない
  // (パン側は対象要素にフォーカスがある時だけ ArrowLeft/Right を preventDefault + stopPropagation
  // するので、このハンドラまでは伝播してこない)。
  useEffect(() => {
    if (!outputNav) return
    const nav = outputNav
    function handleKeyDown(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      if (deleteConfirmOpen || addToInputs.isPending || shareOpen) return
      if (isEditableTarget(document.activeElement)) return
      const targetAssetId = e.key === 'ArrowLeft' ? nav.previousAssetId : nav.nextAssetId
      if (!targetAssetId) return
      e.preventDefault()
      navigate(`/assets/${targetAssetId}`, { replace: true })
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [outputNav, deleteConfirmOpen, addToInputs.isPending, shareOpen, navigate])

  const isReady = !assetQuery.isLoading && !assetQuery.isError && asset !== undefined

  return (
    <div className={styles.page}>
      <div className={styles.viewerArea}>
        {!isReady && (
          <button
            type="button"
            className={`${styles.backButton} ${styles.backButtonFloating}`}
            onClick={goBack}
          >
            {t.viewer.back}
          </button>
        )}
        {assetQuery.isLoading && <p className={styles.placeholder}>{t.viewer.loading}</p>}
        {assetQuery.isError && <p className={styles.placeholder}>{t.viewer.assetLoadError}</p>}
        {isReady && (
          <AssetCanvas
            asset={asset}
            toolbarLeading={
              <button type="button" className={styles.backButton} onClick={goBack}>
                {t.viewer.back}
              </button>
            }
          />
        )}
      </div>

      <aside className={styles.sidebar}>
        {!isReady && <p className={styles.placeholder}>{t.viewer.loading}</p>}
        {isReady && (
          <>
            {asset.deleted_at && (
              <div className={styles.deletedBanner}>
                <p className={styles.deletedBannerText}>
                  {fmt(t.viewer.deletedAt, { date: formatDateTime(asset.deleted_at) })}
                </p>
                {shouldShowRestoreButton(asset.deleted_at, asset.restorable) && (
                  <button
                    type="button"
                    className={styles.restoreButton}
                    onClick={() => restoreMutation.mutate()}
                    disabled={restoreMutation.isPending}
                  >
                    {t.viewer.restoreToStock}
                  </button>
                )}
                {shouldShowNotRestorableNote(asset.deleted_at, asset.restorable) && (
                  <p className={styles.notRestorableNote}>
                    {t.viewer.notRestorableNote}
                  </p>
                )}
                {restoreError && <p className={styles.deleteErrorText}>{restoreError}</p>}
              </div>
            )}
            <h2 className={styles.heading}>Asset</h2>
            {outputNav && (
              <div className={styles.outputNav}>
                <div className={styles.outputNavHeader}>
                  <span className={styles.outputNavLabel}>
                    {fmt(t.viewer.outputPosition, { position: outputNav.position, total: outputNav.total })}
                  </span>
                  <div className={styles.outputNavButtons}>
                    <button
                      type="button"
                      className={styles.outputNavButton}
                      aria-label={t.viewer.previousOutput}
                      disabled={!outputNav.previousAssetId}
                      onClick={() =>
                        outputNav.previousAssetId &&
                        navigate(`/assets/${outputNav.previousAssetId}`, { replace: true })
                      }
                    >
                      ‹
                    </button>
                    <button
                      type="button"
                      className={styles.outputNavButton}
                      aria-label={t.viewer.nextOutput}
                      disabled={!outputNav.nextAssetId}
                      onClick={() =>
                        outputNav.nextAssetId &&
                        navigate(`/assets/${outputNav.nextAssetId}`, { replace: true })
                      }
                    >
                      ›
                    </button>
                  </div>
                </div>
                <div className={styles.outputThumbs}>
                  {outputNav.sorted.map((output, index) => (
                    <button
                      key={output.asset_id}
                      type="button"
                      className={`${styles.outputThumb} ${
                        output.asset_id === asset.id ? styles.outputThumbActive : ''
                      }`}
                      aria-label={fmt(
                        output.asset_id === asset.id
                          ? t.viewer.outputThumbLabelActive
                          : t.viewer.outputThumbLabelInactive,
                        { index: index + 1 },
                      )}
                      aria-current={output.asset_id === asset.id}
                      disabled={output.asset_id === asset.id}
                      onClick={() => navigate(`/assets/${output.asset_id}`, { replace: true })}
                    >
                      <img
                        src={assetUrl(output.asset_id, 'thumb')}
                        alt=""
                        loading="lazy"
                        className={styles.outputThumbImg}
                      />
                    </button>
                  ))}
                </div>
              </div>
            )}
            {supportsAnnotation(asset) && <AssetTitleSection asset={asset} />}
            <dl className={styles.metaList}>
              <dt>{t.viewer.dimensions}</dt>
              <dd>
                {asset.width} × {asset.height} px
              </dd>
              <dt>{t.viewer.format}</dt>
              <dd>{asset.mime}</dd>
              <dt>{t.viewer.size}</dt>
              <dd>{formatBytes(asset.bytes)}</dd>
              <dt>sha256</dt>
              <dd className={styles.mono}>{asset.sha256.slice(0, 16)}...</dd>
              <dt>{t.viewer.created}</dt>
              <dd>{formatDateTime(asset.created_at)}</dd>
            </dl>

            {asset.produced_by_run && (
              <>
                <h3 className={styles.subheading}>{t.viewer.promptHeading}</h3>
                <p className={styles.runPrompt}>{asset.produced_by_run.prompt.slice(0, 80)}</p>
                <p className={styles.runMeta}>
                  {runQuery.data?.model_label ?? asset.produced_by_run.model} ・ {asset.produced_by_run.operation}
                </p>
                {seedText && (
                  <p className={styles.runMeta} title={comfyUiSeedTooltip(runQuery.data?.outputs?.length ?? 0)}>
                    {seedText}
                  </p>
                )}
                <Link to={`/runs/${asset.produced_by_run.id}`} className={styles.runLink}>
                  {t.viewer.viewRunDetail}
                </Link>
                {primaryParentAssetId && (
                  <p className={styles.runMeta}>
                    {t.viewer.sourceImage}{' '}
                    <Link to={`/assets/${primaryParentAssetId}`} className={styles.runLink}>
                      {t.viewer.view}
                    </Link>{' '}
                    ・{' '}
                    <Link
                      to={`/runs/${asset.produced_by_run.id}/compare?before=${primaryParentAssetId}&after=${asset.id}`}
                      className={styles.runLink}
                    >
                      {t.viewer.compareWithOriginal}
                    </Link>
                  </p>
                )}
                {/* 最終プロンプト(ADR-0030 3章)。挿入・置き換えはスタジオへ移って反映する。 */}
                <FinalPromptSection
                  className={styles.finalPrompt}
                  textOutputs={asset.produced_by_run.text_outputs}
                  headingClassName={styles.subheading}
                  renderActions={(text) => <StudioPromptActions prompt={text} />}
                />
              </>
            )}

            {/* タグはプロンプトの下(Run が無い画像では大きさ・形式の下)。多いと畳む。 */}
            {supportsAnnotation(asset) && <AssetTagsSection asset={asset} />}

            {asset.origin && <OriginRecipeSection origin={asset.origin} />}
            {asset.embedded_meta && <EmbeddedMetaSection meta={asset.embedded_meta} />}
            <AssetGroupsSection assetId={asset.id} group={asset.group ?? null} />

            <div className={styles.actions}>
              <a
                className={styles.actionButton}
                href={assetUrl(asset.id, 'original', { download: true })}
              >
                {t.viewer.downloadOriginal}
              </a>
              <button
                type="button"
                className={styles.actionButton}
                onClick={() => addToInputs.request(asset.id)}
                disabled={Boolean(asset.deleted_at)}
              >
                {t.viewer.useAsInput}
              </button>
              <Link
                to={`/lineage/${asset.id}`}
                className={styles.actionButton}
                onClick={(e) => {
                  // デスクトップでサイドバーの系列パネルが既に同じ起点で開いているなら、
                  // 同じグラフが二重に出るだけなので `/lineage` へは遷移せず、サイドバー側を
                  // (既に開いているのでほぼ無害な)フォーカスに留める。
                  if (!isMobileViewport() && selectedPanel === 'graph' && originAssetId === asset.id) {
                    e.preventDefault()
                    openPanel('graph')
                  }
                }}
              >
                {t.viewer.viewLineageGraph}
              </Link>
              {shareEnabled && !asset.deleted_at && (
                <button
                  type="button"
                  className={styles.actionButton}
                  title={t.share.buttonTooltip}
                  onClick={() => setShareOpen(true)}
                >
                  {t.share.button}
                </button>
              )}
              {!asset.deleted_at && (
                <button
                  type="button"
                  className={`${styles.actionButton} ${styles.deleteButton}`}
                  onClick={() => setDeleteConfirmOpen(true)}
                  disabled={deleteMutation.isPending}
                >
                  {t.viewer.delete}
                </button>
              )}
            </div>
            {deleteError && <p className={styles.deleteErrorText}>{deleteError}</p>}
          </>
        )}
      </aside>

      <AddToInputsDialog
        open={addToInputs.isPending}
        onAdd={() => addToInputs.resolve('add')}
        onReplace={() => addToInputs.resolve('replace')}
        onCancel={addToInputs.cancel}
      />
      {isReady && (
        <ConfirmDialog
          open={deleteConfirmOpen}
          message={t.viewer.deleteConfirmMessage}
          previewImageUrl={assetUrl(asset.id, 'thumb')}
          previewDetail={`${asset.width} × ${asset.height}`}
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setDeleteConfirmOpen(false)}
        />
      )}
      <ShareDialog open={shareOpen} assetId={assetId} onClose={() => setShareOpen(false)} toast={toast} />
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
  )
}

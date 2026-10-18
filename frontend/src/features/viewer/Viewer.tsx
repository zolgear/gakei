/**
 * 4K ビューア。パン/ズーム表示は `AssetCanvas`(スタジオの結果プレビューと共用)に委ねる。
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate } from 'react-router'
import { ApiError, deleteAsset, getAsset, getRun, getShareSettings, restoreAsset } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { useResourcePanel } from '../../context/useResourcePanel'
import { useBackNavigate } from '../../lib/useBackNavigate'
import { isMobileViewport, useIsMobileViewport } from '../../lib/viewport'
import { useAddToInputs } from '../run-form/useAddToInputs'
import { AddToInputsDialog } from '../run-form/AddToInputsDialog'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import {
  AddToInputIcon,
  DownloadIcon,
  ExportArchiveIcon,
  LineageIcon,
  RunDetailIcon,
  ShareLinkIcon,
  TrashIcon,
} from '../../components/icons'
import { ToastHost, useToast } from '../../components/Toast'
import { formatDateTime } from '../../lib/format'
import { shouldShowNotRestorableNote, shouldShowRestoreButton } from '../../lib/assetRestore'
import { fmt, useI18n } from '../../i18n'
import { annotationPollInterval } from '../annotations/annotationStatus'
import { contentRatingPollInterval } from '../content-rating/contentRating'
import { AssetThumb } from '../content-rating/AssetThumb'
import { resolveRunOutputNav } from './runOutputs'
import { AssetCanvas } from './AssetCanvas'
import { AssetDetailSections } from './AssetDetailSections'
import { ShareDialog } from '../share/ShareDialog'
import { ExportLineageDialog } from '../lineage-transfer/ExportLineageDialog'
import { importToastFromState } from '../lineage-transfer/lineageTransfer'
import { SHARE_SETTINGS_QUERY_KEY } from '../settings/queryKeys'
import { hasDenyVerdict, hasFlagVerdict, isSendBlocked, isWithheld } from '../mods/modVerdicts'
import { VerdictBadge } from '../mods/VerdictList'
import styles from './Viewer.module.css'
import { focalStyle } from '../../lib/focalPoint'

interface ViewerProps {
  assetId: string
}

/**
 * 生成元 Run の詳細を取得する共通フック。出力一覧(他の出力への切り替え用)を導く。queryKey は
 * `AssetDetailSections`(主たる親 Asset と seed)と共有し、二重に取得しない。
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
  const isMobile = useIsMobileViewport()
  // 推定中(ADR-0024)とレーティングの判定中(ADR-0049)は、終わるまで取り直す。
  const assetQuery = useQuery({
    queryKey: ['asset', assetId],
    queryFn: () => getAsset(assetId),
    refetchInterval: (query) =>
      annotationPollInterval(query.state.data) || contentRatingPollInterval(query.state.data),
  })
  const { originAssetId, setOriginAssetId } = useLineageOrigin()
  const { selectedPanel, openPanel } = useResourcePanel()
  const goBack = useBackNavigate('/')
  const navigate = useNavigate()
  const addToInputs = useAddToInputs()
  // 「配置で開く」(ADR-0050 5章)。保存するとフォームの入力に置いてスタジオへ移る。
  const queryClient = useQueryClient()
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [shareOpen, setShareOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
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

  // 系列の取り込み(ADR-0037)から移ってきたときのトースト。一度出したら state を消し、
  // 戻る・再読み込みで出し直さない。
  const location = useLocation()
  useEffect(() => {
    const message = importToastFromState(location.state)
    if (!message) return
    toast.show({ message })
    navigate(`${location.pathname}${location.search}`, { replace: true, state: null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key])

  // 系列グラフの起点(a): ビューアで開いている Asset を自動的に起点にする。
  useEffect(() => {
    setOriginAssetId(assetId)
  }, [assetId, setOriginAssetId])

  const asset = assetQuery.data
  const runQuery = useRun(asset?.produced_by_run?.id)
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
      if (deleteConfirmOpen || addToInputs.isPending || shareOpen || exportOpen) return
      if (isEditableTarget(document.activeElement)) return
      const targetAssetId = e.key === 'ArrowLeft' ? nav.previousAssetId : nav.nextAssetId
      if (!targetAssetId) return
      e.preventDefault()
      navigate(`/assets/${targetAssetId}`, { replace: true })
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [outputNav, deleteConfirmOpen, addToInputs.isPending, shareOpen, exportOpen, navigate])

  const isReady = !assetQuery.isLoading && !assetQuery.isError && asset !== undefined
  // 画像を残さなかった(ADR-0051 4章)。画像を使う操作(ダウンロード、共有、入力に使う)を止める。
  const withheld = isWithheld(asset)
  // 入力に使えない(画像なし、または最新の判定に deny がある)。
  const sendBlocked = isSendBlocked(asset)

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
            contentRating={asset.content_rating ?? null}
            withheld={withheld}
            withheldLabel={asset.content_withheld_verdict?.label}
            withheldMessage={asset.content_withheld_verdict?.message}
            onImageError={() => queryClient.invalidateQueries({ queryKey: ['asset', asset.id] })}
            // 狭い幅では画像の枠の下に情報欄が続くので、枠の上でもページを縦にスクロールできるように
            // する(全体表示のときだけ。拡大したらパン)。
            pageScrollWhenFit={isMobile}
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
            {/* 見出しの行はパネルの上端に固定し、よく使う操作をアイコンで並べる(削除だけは末尾)。 */}
            <div className={styles.headerRow}>
              <h2 className={styles.heading}>Asset</h2>
              {/* Mod の判定の印(ADR-0051 5章)。拒否を優先し、無ければ要確認。 */}
              {(hasDenyVerdict(asset) || hasFlagVerdict(asset)) && (
                <span className={styles.verdictMark} title={hasDenyVerdict(asset) ? undefined : t.mods.flagMark}>
                  <VerdictBadge verdict={hasDenyVerdict(asset) ? 'deny' : 'flag'} />
                </span>
              )}
              <div className={styles.headerActions}>
                {/* 生成元の Run の詳細へ直接移る(下の文字のリンクと同じ遷移先)。生成元がある画像
                    だけに出る。並びは右寄せなので、左端に置けば有無でほかのボタンの位置は変わらない。 */}
                {asset.produced_by_run && (
                  <Link
                    to={`/runs/${asset.produced_by_run.id}`}
                    className={styles.iconButton}
                    aria-label={t.common.openGeneratedDetail}
                    title={t.common.openGeneratedDetail}
                  >
                    <RunDetailIcon />
                  </Link>
                )}
                {withheld ? (
                  // 画像が無いのでダウンロードできない(位置を変えないよう、押せないボタンで残す)。
                  <button
                    type="button"
                    className={styles.iconButton}
                    aria-label={t.viewer.downloadOriginal}
                    title={t.mods.withheldUnavailable}
                    disabled
                  >
                    <DownloadIcon />
                  </button>
                ) : (
                  <a
                    className={styles.iconButton}
                    href={assetUrl(asset.id, 'original', { download: true })}
                    aria-label={t.viewer.downloadOriginal}
                    title={t.viewer.downloadOriginal}
                  >
                    <DownloadIcon />
                  </a>
                )}
                <button
                  type="button"
                  className={styles.iconButton}
                  aria-label={t.viewer.useAsInput}
                  title={sendBlocked ? t.mods.cannotUseAsInput : t.viewer.useAsInput}
                  onClick={() => addToInputs.request(asset.id)}
                  disabled={Boolean(asset.deleted_at) || sendBlocked}
                >
                  <AddToInputIcon />
                </button>
                <Link
                  to={`/lineage/${asset.id}`}
                  className={styles.iconButton}
                  aria-label={t.viewer.viewLineageGraph}
                  title={t.viewer.viewLineageGraph}
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
                  <LineageIcon />
                </Link>
                {/* 系列を ZIP に書き出す(ADR-0037)。共有リンクと違い管理者設定は無い。
                    削除済みと画像なし(ADR-0051)では押せない状態で残す(アイコンの位置を変えないため)。 */}
                <button
                  type="button"
                  className={styles.iconButton}
                  aria-label={t.lineageTransfer.export.button}
                  title={withheld ? t.mods.withheldUnavailable : t.lineageTransfer.export.buttonTooltip}
                  onClick={() => setExportOpen(true)}
                  disabled={Boolean(asset.deleted_at) || withheld}
                >
                  <ExportArchiveIcon />
                </button>
                {/* 共有は管理者が無効にしていれば出さない(画像によらず同じなので並びは変わらない)。 */}
                {shareEnabled && (
                  <button
                    type="button"
                    className={styles.iconButton}
                    aria-label={t.share.button}
                    title={withheld ? t.mods.withheldUnavailable : t.share.buttonTooltip}
                    onClick={() => setShareOpen(true)}
                    disabled={Boolean(asset.deleted_at) || withheld}
                  >
                    <ShareLinkIcon />
                  </button>
                )}
              </div>
            </div>
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
                      <AssetThumb
                        assetId={output.asset_id}
                        rating={output.content_rating}
                        withheld={output.content_withheld}
                        style={focalStyle(output.focal_point)}
                        alt=""
                        loading="lazy"
                        className={styles.outputThumbImg}
                      />
                    </button>
                  ))}
                </div>
              </div>
            )}
            {/* タイトル、寸法など、生成元、タグ、レーティングなど(スタジオの「詳細」タブ・系列インスペクターと共通)。 */}
            <AssetDetailSections asset={asset} />

            {!asset.deleted_at && (
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.deleteButton}
                  onClick={() => setDeleteConfirmOpen(true)}
                  disabled={deleteMutation.isPending}
                >
                  <TrashIcon />
                  {t.viewer.delete}
                </button>
              </div>
            )}
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
          previewImageUrl={withheld ? undefined : assetUrl(asset.id, 'thumb')}
          previewFocalPoint={asset.focal_point}
          previewDetail={`${asset.width} × ${asset.height}`}
          onConfirm={() => deleteMutation.mutate()}
          onCancel={() => setDeleteConfirmOpen(false)}
        />
      )}
      <ShareDialog open={shareOpen} assetId={assetId} onClose={() => setShareOpen(false)} toast={toast} />
      <ExportLineageDialog
        open={exportOpen}
        assetId={assetId}
        onClose={() => setExportOpen(false)}
        toast={toast}
      />
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
  )
}

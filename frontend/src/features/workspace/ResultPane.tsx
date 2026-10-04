/**
 * スタジオ上段(結果エリア)。「プレビュー / 系列」を切り替える。プレビューは表示中の Asset
 * (`displayedAssetId`。URL の `?asset=` と同期、親が管理)か、実行中/直前の Run
 * (`pendingRunId`。URL の `?run=` と同期、親が管理)の進捗・結果を表示する。系列は
 * `LineageGraph` を埋め込み、ノードのタップ= 選択(遷移しない。`?node=` と同期)、
 * ダブルタップ/「ノードをプレビュー」=表示切り替え。選択したノードは `LineageInspectorPanel`
 * (`/lineage` と共有)でその場に開き、Run ならプロンプトを下段の textarea に挿入/置き換えできる。
 *
 * `pendingRunId` の status は `StudioReturnContext` へ報告する(タブを離れて Studio に
 * 戻ったときの復元判定に使う。ADR-0009)。
 *
 * ヘッダー右上には `StudioLayoutToggleButton`(入力欄の配置切り替え。ADR-0009 1章・
 * 2026-09-26 追記)を置く。狭い幅(<768px)では非表示にする(CSS 側)。
 */
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate } from 'react-router'
import {
  ApiError,
  cancelRun,
  deleteAsset,
  getAsset,
  getAssetLineage,
  getCapabilities,
  getRun,
  listAssets,
  restoreAsset,
} from '../../api/client'
import { assetUrl, runPartialUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { useStudioReturn } from '../../context/useStudioReturn'
import { editMaxInputImages } from '../../lib/capabilities'
import { useRunEvents } from '../run-status/useRunEvents'
import { latestPartialsPerOutput, progressPercent } from '../run-status/runProgress'
import { computeExpectedPartials } from '../favicon/expectedPartials'
import { setWatchedRun, type WatchedRunStatus } from '../favicon/watchedRunStore'
import { shouldInvalidateAssetsOnStatusChange } from '../run-status/assetInvalidation'
import { describeError } from '../run-status/errorMessages'
import { AssetCanvas } from '../viewer/AssetCanvas'
import { LineageGraph } from '../lineage/LineageGraph'
import { LineageInspectorPanel } from '../lineage/LineageInspectorPanel'
import { StudioLayoutToggleButton } from './StudioLayoutToggleButton'
import type { StudioLayout } from './studioLayout'
import { resolveInspectorContent } from '../lineage/inspectorContent'
import { computeInspectorWidthPx, resolveInspectorPlacement } from '../lineage/inspectorPlacement'
import { parseNodeIdFromSearch } from '../lineage/nodeQueryParam'
import type { InsertPromptFn } from '../run-form/promptInsertion'
import { RunPromptActions } from './RunPromptActions'
import { RunDetailContent } from '../run-detail/RunDetailContent'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { ToastHost, useToast } from '../../components/Toast'
import { formatDuration, shortenModelName, statusLabel } from '../../lib/format'
import { formatTokenCountWithCost } from '../history/historyChips'
import { resolveChoiceLabel } from '../run-form/paramLabels'
import { shouldShowNotRestorableNote, shouldShowRestoreButton } from '../../lib/assetRestore'
import { addAsInput, isUsedAsInput } from './useAsInputToggle'
import { addImageInputs } from '../run-form/editInputs'
import { resolveResultOrigin } from './resultOrigin'
import { buildStudioPath } from './assetQueryParam'
import { invalidateAssetGroupQueries } from '../stock/groups/assetGroupQueries'
import styles from './ResultPane.module.css'

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'canceled'])
const FALLBACK_POLL_INTERVAL_MS = 3000
const RECENT_OUTPUTS_FETCH_LIMIT = 8
const RECENT_OUTPUTS_SHOWN = 5
const ADDED_NOTICE_MS = 2000

interface ResultPaneProps {
  displayedAssetId: string | null
  pendingRunId: string | null
  onSelectAsset: (assetId: string | null, opts?: { replace?: boolean }) => void
  /** 系列インスペクターの「プロンプトに挿入/置き換え」の実体(`InputPane` から受け渡される)。 */
  insertPrompt: InsertPromptFn
  /**
   * 実効の配置。サイドバー配置では右にストックがあり「出力・直近」の枠と役割が重なるので
   * 出さない(「入力に使う」は画像下の操作バーにある。2026-09-26)。
   */
  layout: StudioLayout
}

function formatSize(size: unknown): string | null {
  return typeof size === 'string' && size.length > 0 ? size.replace('x', ' × ') : null
}

export function ResultPane({
  displayedAssetId,
  pendingRunId,
  onSelectAsset,
  insertPrompt,
  layout,
}: ResultPaneProps) {
  const { t } = useI18n()
  const rp = t.workspace.resultPane
  const navigate = useNavigate()
  const location = useLocation()
  const [viewMode, setViewMode] = useState<'preview' | 'graph'>('preview')
  const queryClient = useQueryClient()
  const { formState, setFormState } = useRunFormContext()
  const studioReturn = useStudioReturn()
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })

  // 系列モードでノードをタップして選んだノード(Asset/Run 共通)。`?node=` と同期し、
  // 戻る/進むで再現する。ダブルタップ/「ノードをプレビュー」でプレビューモードへ切り替えたら
  // `onSelectAsset` が `node` の付かない URL を作るので自然に外れる。
  const selectedNodeId = parseNodeIdFromSearch(location.search)

  // インスペクター(グラフに重ねるオーバーレイ)の配置は、上段の実高さと画面幅から決める。
  const paneRef = useRef<HTMLDivElement | null>(null)
  const [paneMetrics, setPaneMetrics] = useState(() => ({
    height: 0,
    width: 0,
    top: 0,
    viewportWidth: typeof window !== 'undefined' ? window.innerWidth : 1024,
  }))
  useEffect(() => {
    const el = paneRef.current
    if (!el) return
    function measure() {
      const rect = el!.getBoundingClientRect()
      setPaneMetrics({ height: rect.height, width: rect.width, top: rect.top, viewportWidth: window.innerWidth })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    window.addEventListener('resize', measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [])
  const inspectorPlacement = resolveInspectorPlacement(paneMetrics.height, paneMetrics.viewportWidth)

  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [addedNotice, setAddedNotice] = useState(false)
  const [toggleError, setToggleError] = useState<string | null>(null)
  const addedNoticeTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const toast = useToast()

  // --- 実行中/直前の Run(まだ表示できる出力が無い間だけ関係する) ---
  const { latestStatus, partials, progress, connection } = useRunEvents(pendingRunId)
  const pendingRunQuery = useQuery({
    queryKey: ['run', pendingRunId],
    queryFn: () => getRun(pendingRunId as string),
    enabled: pendingRunId !== null,
    refetchInterval: (query) => {
      if (connection !== 'error') return false
      const data = query.state.data
      if (!data || TERMINAL_STATUSES.has(data.status)) return false
      return FALLBACK_POLL_INTERVAL_MS
    },
  })
  const sseStatus = latestStatus?.status ?? null
  const isTerminalFromSse = sseStatus !== null && TERMINAL_STATUSES.has(sseStatus)
  const preferSse = connection !== 'error'
  const pendingStatus = pendingRunId
    ? (preferSse ? (sseStatus ?? pendingRunQuery.data?.status ?? null) : (pendingRunQuery.data?.status ?? sseStatus ?? null))
    : null

  // 観測した status を StudioReturnContext へ報告する(タブを離れて Studio に戻ったときの
  // 復元判定に使う。ADR-0009)。
  useEffect(() => {
    if (!pendingRunId || !pendingStatus) return
    studioReturn.reportStatus(pendingRunId, pendingStatus)
  }, [pendingRunId, pendingStatus, studioReturn])

  // favicon / App バーのロゴの進捗表示(ADR-0009 8章)へ、見ている Run の情報を渡す。
  // pendingRunId が無いときは(依存の変化のたびにではなく)ここで null に戻す。
  // 成功・失敗の演出は、実行中・待機中だったのをこのタブで見た Run にだけ出す(完了済みの Run を
  // 開き直しただけでは出さない)ので、そう観測した runId を覚えておく。
  const observedActiveRunIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (!pendingRunId) {
      setWatchedRun(null)
      return
    }
    if (pendingStatus === 'queued' || pendingStatus === 'running') {
      observedActiveRunIdRef.current = pendingRunId
    }
    const watchedProgress =
      progress && progress.value != null && progress.max != null
        ? { value: progress.value, max: progress.max }
        : null
    setWatchedRun({
      runId: pendingRunId,
      status: (pendingStatus as WatchedRunStatus) ?? null,
      connection,
      progress: watchedProgress,
      partialCount: partials.length,
      expectedPartials: computeExpectedPartials(pendingRunQuery.data?.params),
      observedActive: observedActiveRunIdRef.current === pendingRunId,
    })
  }, [pendingRunId, pendingStatus, connection, progress, partials.length, pendingRunQuery.data?.params])

  // アンマウント時だけ null に戻す(上の effect は依存の変化のたびに走るので、そちらに
  // クリーンアップを持たせると更新のたびに一瞬 null を挟んでしまう)。
  useEffect(() => {
    return () => setWatchedRun(null)
  }, [])

  useEffect(() => {
    if (!pendingRunId) return
    if (isTerminalFromSse || connection === 'error') {
      queryClient.invalidateQueries({ queryKey: ['run', pendingRunId] })
      queryClient.invalidateQueries({ queryKey: ['runs'] })
    }
  }, [isTerminalFromSse, connection, pendingRunId, queryClient])

  // succeeded に変わった瞬間だけストックを invalidate する(失敗・中止では不要)。
  const prevPendingStatusRef = useRef<string | null>(null)
  useEffect(() => {
    if (!pendingRunId) {
      prevPendingStatusRef.current = null
      return
    }
    if (shouldInvalidateAssetsOnStatusChange(prevPendingStatusRef.current, pendingStatus)) {
      // ['assets'] に加えて、生成時にグループを指定していれば出力がそのグループに入る
      // (ADR-0022)ので、グループ一覧(枚数・表紙)も取り直す。
      invalidateAssetGroupQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['run', pendingRunId] })
      // 出力 Asset が系列に増えるので、サイドバー・系列モードのグラフも取り直す。
      queryClient.invalidateQueries({ queryKey: ['lineage'] })
    }
    prevPendingStatusRef.current = pendingStatus
  }, [pendingStatus, pendingRunId, queryClient])

  // succeeded になり、出力が届いたら、先頭の出力をそのまま表示に切り替える(既にそれを
  // 表示中なら呼び直さない。onSelectAsset は StudioWorkspace 側で毎レンダー新しい関数に
  // なるため、依存に入れて無限に呼び直さないためのガード)。切り替えは Run ごとに1回だけで、
  // その後に利用者がサムネイルや入力のチップで別の画像を選んでも、先頭の出力へ引き戻さない。
  // 切り替えるのは「この画面で queued/running を見届けた Run」だけ(observedActiveRunIdRef)。
  // 完了済みの Run を `/studio?run=` で開いた場合(系列パネルで Generated を選んだ等)は
  // 切り替えず、下の showRunDetail で Run 詳細を結果エリアに出す。
  const autoSelectedRunIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (pendingStatus !== 'succeeded' || pendingRunQuery.data?.status !== 'succeeded') return
    if (observedActiveRunIdRef.current !== pendingRunQuery.data.id) return
    if (autoSelectedRunIdRef.current === pendingRunQuery.data.id) return
    const first = pendingRunQuery.data.outputs?.[0]
    if (!first) return
    autoSelectedRunIdRef.current = pendingRunQuery.data.id
    if (first.asset_id !== displayedAssetId) {
      onSelectAsset(first.asset_id, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingStatus, pendingRunQuery.data, displayedAssetId])

  const cancelMutation = useMutation({
    mutationFn: () => cancelRun(pendingRunId as string),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['run', pendingRunId] })
      queryClient.invalidateQueries({ queryKey: ['runs'] })
    },
  })

  // --- 表示の起点(?asset= > 実行中/直前の Run > 入力の主たる親 > 空状態) ---
  const primaryParentAssetId =
    formState.inputs.find((i) => i.role === 'image' && i.position === 0)?.assetId ?? null
  const origin = resolveResultOrigin({
    assetParam: displayedAssetId,
    hasPendingRun: pendingRunId !== null,
    primaryParentAssetId,
  })
  const effectiveAssetId = origin.assetId

  // `?asset=` が無く、追っている Run が終わっているときは Run 詳細(プロンプト・出力・設定)を
  // 結果エリアに出す(2026-09-26。系列パネルやストックから Run を選んで、入力欄を残したまま
  // 参照するため)。この画面で実行を見届けた Run は除く(成功は先頭の出力へ切り替え済み、
  // 失敗・中止は従来のエラー表示)。ただし出力へ切り替え済みの Run を選び直したときは出す。
  const pendingTerminal = pendingStatus !== null && TERMINAL_STATUSES.has(pendingStatus)
  const showRunDetail =
    !effectiveAssetId &&
    pendingRunId !== null &&
    pendingTerminal &&
    (observedActiveRunIdRef.current !== pendingRunId || autoSelectedRunIdRef.current === pendingRunId)

  // サイドバーの系列パネルに、メインに表示中のプレビューを「現在地」として伝える
  // (ビューア・Run 詳細と同じ経路。生成後に先頭の出力へ切り替わったときも追随する)。
  const { setOriginAssetId } = useLineageOrigin()
  useEffect(() => {
    if (effectiveAssetId) setOriginAssetId(effectiveAssetId)
  }, [effectiveAssetId, setOriginAssetId])

  // 系列モードのインスペクター用: 選択中ノードが Run/Asset のどちらかをグラフのノード一覧から
  // 判定する(`LineageGraph` 自身のクエリと同じ queryKey なのでキャッシュを共有する)。
  const lineageQuery = useQuery({
    queryKey: ['lineage', effectiveAssetId],
    queryFn: () => getAssetLineage(effectiveAssetId as string),
    enabled: effectiveAssetId !== null && viewMode === 'graph',
  })
  const inspectorContent = resolveInspectorContent(selectedNodeId, lineageQuery.data?.nodes ?? [])
  // パネルが実際に開いているときだけグラフをずらす(閉じていれば 0 = 補正なし)。
  const inspectorWidthPx =
    inspectorContent.kind === 'none'
      ? 0
      : computeInspectorWidthPx(inspectorPlacement, paneMetrics.width, paneMetrics.viewportWidth)

  function selectNode(id: string | null) {
    navigate(buildStudioPath(displayedAssetId, id))
  }

  // 「入力の主たる親」を表示しているときは、その Asset に生成元 Run があっても Run の情報
  // ではなく「入力」であることを示す(ユーザーが今から使う入力の確認が目的のため)。
  const isInputPreview = origin.kind === 'primary-input'

  // --- 表示中の Asset ---
  const assetQuery = useQuery({
    queryKey: ['asset', effectiveAssetId],
    queryFn: () => getAsset(effectiveAssetId as string),
    enabled: effectiveAssetId !== null,
  })
  const asset = assetQuery.data
  const ownerRunId = asset?.produced_by_run?.id
  const ownerRunQuery = useQuery({
    queryKey: ['run', ownerRunId],
    queryFn: () => getRun(ownerRunId as string),
    enabled: ownerRunId !== undefined,
  })
  const ownerRun = ownerRunQuery.data

  const recentOutputsQuery = useQuery({
    queryKey: ['assets', 'generated', 'recent-for-studio'],
    queryFn: () => listAssets({ kind: ['generated'], limit: RECENT_OUTPUTS_FETCH_LIMIT }),
    enabled: effectiveAssetId !== null,
  })

  const currentRunOutputs = ownerRun?.outputs ?? []
  const currentRunOutputIds = new Set(currentRunOutputs.map((o) => o.asset_id))
  const recentOutputs = (recentOutputsQuery.data?.items ?? [])
    .filter((a) => !currentRunOutputIds.has(a.id) && a.id !== effectiveAssetId)
    .slice(0, RECENT_OUTPUTS_SHOWN)

  const deleteMutation = useMutation({
    mutationFn: () => deleteAsset(effectiveAssetId as string),
    onSuccess: () => {
      const deletedAssetId = effectiveAssetId
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      if (deletedAssetId) queryClient.invalidateQueries({ queryKey: ['asset', deletedAssetId] })
      setDeleteConfirmOpen(false)
      onSelectAsset(null)
      if (deletedAssetId) {
        toast.show({
          message: rp.deletedToast,
          actionLabel: rp.undoAction,
          onAction: () => restoreMutation.mutate(deletedAssetId),
        })
      }
    },
    onError: (err: unknown) => {
      setDeleteConfirmOpen(false)
      setDeleteError(err instanceof ApiError ? err.message : rp.deleteFailedDefault)
    },
  })

  const restoreMutation = useMutation({
    mutationFn: (assetId: string) => restoreAsset(assetId),
    onSuccess: (_data, assetId) => {
      setRestoreError(null)
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['asset', assetId] })
    },
    onError: (err: unknown) => {
      setRestoreError(err instanceof ApiError ? err.message : rp.restoreFailedDefault)
    },
  })

  useEffect(() => {
    return () => {
      if (addedNoticeTimeoutRef.current) clearTimeout(addedNoticeTimeoutRef.current)
    }
  }, [])

  // 「入力に使う」の上限は、現在フォームで選ばれているモデルの edit 上限を使う。
  const maxInputImagesForToggle = editMaxInputImages(capsQuery.data, formState.provider, formState.model)
  const usedAsInput = effectiveAssetId ? isUsedAsInput(formState.inputs, effectiveAssetId) : false

  // 追加のみ。使用中のときはボタンを無効にし、外すのは入力欄のチップから行う。
  function handleUseAsInput() {
    if (!effectiveAssetId || usedAsInput) return
    setToggleError(null)
    const result = addAsInput(formState.inputs, effectiveAssetId, maxInputImagesForToggle)
    setFormState({ ...formState, inputs: result.inputs })
    if (result.added) {
      setAddedNotice(true)
      if (addedNoticeTimeoutRef.current) clearTimeout(addedNoticeTimeoutRef.current)
      addedNoticeTimeoutRef.current = setTimeout(() => setAddedNotice(false), ADDED_NOTICE_MS)
    } else if (result.rejected) {
      setToggleError(fmt(rp.inputLimitReached, { max: maxInputImagesForToggle }))
    }
  }

  // サムネイル列の各タイルの「+」(ストックのタイルと同じ、常に追加のみ・確認なし)。
  function handleAddThumbnailAsInput(assetId: string) {
    setToggleError(null)
    const result = addImageInputs(formState.inputs, [assetId], maxInputImagesForToggle)
    if (result.addedCount === 0) {
      setToggleError(fmt(rp.inputLimitReached, { max: maxInputImagesForToggle }))
      return
    }
    setFormState({ ...formState, inputs: result.inputs })
    setAddedNotice(true)
    if (addedNoticeTimeoutRef.current) clearTimeout(addedNoticeTimeoutRef.current)
    addedNoticeTimeoutRef.current = setTimeout(() => setAddedNotice(false), ADDED_NOTICE_MS)
  }

  const headerRun = isInputPreview ? undefined : effectiveAssetId ? ownerRun : pendingRunQuery.data
  const headerStatus = isInputPreview ? undefined : effectiveAssetId ? ownerRun?.status : pendingStatus
  const metaParts: string[] = []
  if (headerRun) {
    metaParts.push(`Generated ${headerRun.id.slice(0, 8)}`)
    metaParts.push(shortenModelName(headerRun.model))
    const sizeLabel = formatSize((headerRun.params as Record<string, unknown> | undefined)?.size)
    if (sizeLabel) metaParts.push(sizeLabel)
    const quality = (headerRun.params as Record<string, unknown> | undefined)?.quality
    if (typeof quality === 'string') {
      metaParts.push(resolveChoiceLabel(capsQuery.data, headerRun.model, headerRun.operation, 'quality', quality))
    }
    if (headerRun.finished_at) metaParts.push(formatDuration(headerRun.started_at, headerRun.finished_at))
    const tok = formatTokenCountWithCost(headerRun.usage, headerRun.cost_usd)
    if (tok) metaParts.push(tok)
  }
  const metaLineText =
    isInputPreview && asset ? fmt(rp.inputPrefix, { width: asset.width, height: asset.height }) : metaParts.join(' · ')

  return (
    <div className={styles.pane} ref={paneRef}>
      <div className={styles.header}>
        <div className={styles.modeGroup} role="group" aria-label={rp.viewModeGroup}>
          <button
            type="button"
            aria-pressed={viewMode === 'preview'}
            className={styles.modeButton}
            onClick={() => {
              if (selectedNodeId) selectNode(null)
              setViewMode('preview')
            }}
          >
            {rp.preview}
          </button>
          <button
            type="button"
            aria-pressed={viewMode === 'graph'}
            className={styles.modeButton}
            onClick={() => {
              if (selectedNodeId) selectNode(null)
              setViewMode('graph')
            }}
            disabled={!effectiveAssetId}
          >
            {rp.lineage}
          </button>
        </div>

        {metaLineText && <span className={styles.metaLine}>{metaLineText}</span>}

        {asset?.deleted_at && (
          <span className={styles.deletedBadge}>{rp.deletedBadge}</span>
        )}
        {asset && shouldShowRestoreButton(asset.deleted_at, asset.restorable) && (
          <button
            type="button"
            className={styles.restoreButton}
            onClick={() => restoreMutation.mutate(asset.id)}
            disabled={restoreMutation.isPending}
          >
            {rp.restore}
          </button>
        )}
        {asset && shouldShowNotRestorableNote(asset.deleted_at, asset.restorable) && (
          <span className={styles.notRestorableNote}>{rp.notRestorable}</span>
        )}

        {headerStatus && (
          <span className={styles.statusLine}>
            <span className={styles.statusDot} data-status={headerStatus} aria-hidden="true" />
            {statusLabel(headerStatus)}
          </span>
        )}

        <div className={styles.headerSpacer} />

        <StudioLayoutToggleButton className={`${styles.headerIconButton} ${styles.layoutToggle}`} />

        {/* スタジオ内では Run 詳細ページへ移動せず、結果エリアに Run 詳細を出す(`?run=`)。
            既に Run 詳細を表示中なら出さない。フルページの Run 詳細は履歴から開ける。 */}
        {headerRun && !showRunDetail && (
          <Link to={buildStudioPath(null, null, headerRun.id)} className={styles.headerButton}>
            {rp.generatedDetail}
          </Link>
        )}
        {asset && (
          <>
            <a
              className={styles.headerIconButton}
              aria-label={rp.downloadOriginal}
              title={rp.downloadOriginal}
              href={assetUrl(asset.id, 'original', { download: true })}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M8 2v9M4.5 7.5L8 11l3.5-3.5M3 13.5h10"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </a>
            {!asset.deleted_at && (
              <button
                type="button"
                className={`${styles.headerIconButton} ${styles.headerIconButtonDanger}`}
                aria-label={rp.deleteCurrentAsset}
                title={rp.deleteCurrentAsset}
                onClick={() => setDeleteConfirmOpen(true)}
                // 系列表示中は「表示中の画像」が視覚的に分かりにくい(グラフのどのノードが
                // 対象か紛らわしい)ため無効にする。削除したいときはノードを選んでプレビュー
                // に戻ってから。
                disabled={deleteMutation.isPending || viewMode === 'graph'}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M3 4.5h10M6.5 4V2.5h3V4M4.5 4.5l.7 9h5.6l.7-9M6.7 7v4.5M9.3 7v4.5"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            )}
          </>
        )}
      </div>

      {deleteError && <p className={styles.deleteError}>{deleteError}</p>}
      {restoreError && <p className={styles.deleteError}>{restoreError}</p>}

      {viewMode === 'graph' ? (
        effectiveAssetId ? (
          <div className={styles.body}>
            <div className={styles.graphCanvasWrap}>
              <LineageGraph
                assetId={effectiveAssetId}
                height="100%"
                highlightedNodeId={selectedNodeId}
                showPreviewButton
                inspectorWidthPx={inspectorWidthPx}
                onNodeClick={(node) => selectNode(node.id)}
                onNodeDoubleClick={(node) => {
                  if (node.type === 'asset') {
                    onSelectAsset(node.id)
                    setViewMode('preview')
                  }
                }}
              />
              <LineageInspectorPanel
                content={inspectorContent}
                onClose={() => selectNode(null)}
                placement={inspectorPlacement}
                mainRightTop={inspectorPlacement === 'main-right' ? paneMetrics.top : undefined}
                renderPromptActions={(prompt) => (
                  <RunPromptActions
                    prompt={prompt}
                    currentPrompt={formState.prompt}
                    insertPrompt={insertPrompt}
                  />
                )}
              />
            </div>
          </div>
        ) : (
          <p className={styles.emptyState}>{rp.lineageEmptyState}</p>
        )
      ) : (
        <div className={styles.body}>
          {effectiveAssetId && asset && (
            <>
              <div className={styles.canvasWrap}>
                <AssetCanvas asset={asset} />
                <div className={styles.canvasActionBar}>
                  <button
                    type="button"
                    className={styles.canvasUseAsInputButton}
                    data-active={usedAsInput}
                    disabled={usedAsInput}
                    title={usedAsInput ? rp.inUseHint : undefined}
                    onClick={handleUseAsInput}
                  >
                    {usedAsInput ? rp.inUse : rp.useAsInput}
                  </button>
                  <a
                    className={styles.canvasIconButton}
                    aria-label={rp.downloadOriginal}
                    title={rp.downloadOriginal}
                    href={assetUrl(asset.id, 'original', { download: true })}
                  >
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                      <path
                        d="M8 2v9M4.5 7.5L8 11l3.5-3.5M3 13.5h10"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </a>
                </div>
              </div>
              {/* サイドバー配置では右のストックと役割が重なり、「入力に使う」も画像下の
                  操作バー(canvasActionBar)にあるので、この枠ごと出さない。 */}
              {layout !== 'sidebar' && (
              <div className={styles.sidebarThumbs}>
                {(currentRunOutputs.length > 0 || recentOutputs.length > 0) && (
                  <p className={styles.thumbsHeading}>{rp.outputsRecent}</p>
                )}
                <div className={styles.thumbsScroll}>
                  {currentRunOutputs.map((output) => (
                    <div key={output.asset_id} className={styles.thumbTile}>
                      <button
                        type="button"
                        className={styles.thumbButton}
                        aria-pressed={output.asset_id === effectiveAssetId}
                        aria-label={fmt(rp.outputIndexAria, { index: (output.output_index ?? 0) + 1 })}
                        onClick={() => onSelectAsset(output.asset_id)}
                      >
                        <img
                          className={styles.thumbImage}
                          src={assetUrl(output.asset_id, 'thumb')}
                          alt=""
                          draggable={false}
                        />
                        <span className={styles.thumbCurrentBadge}>{rp.thisRun}</span>
                      </button>
                      <button
                        type="button"
                        className={styles.thumbAddButton}
                        aria-label={rp.addToInputAria}
                        onClick={() => handleAddThumbnailAsInput(output.asset_id)}
                      >
                        +
                      </button>
                    </div>
                  ))}
                  {recentOutputs.map((item) => (
                    <div key={item.id} className={styles.thumbTile}>
                      <button
                        type="button"
                        className={styles.thumbButton}
                        aria-pressed={item.id === effectiveAssetId}
                        aria-label={rp.showThisOutputAria}
                        onClick={() => onSelectAsset(item.id)}
                      >
                        <img className={styles.thumbImage} src={assetUrl(item.id, 'thumb')} alt="" draggable={false} />
                      </button>
                      <button
                        type="button"
                        className={styles.thumbAddButton}
                        aria-label={rp.addToInputAria}
                        onClick={() => handleAddThumbnailAsInput(item.id)}
                      >
                        +
                      </button>
                    </div>
                  ))}
                </div>
                <div className={styles.thumbsFooter}>
                  <button
                    type="button"
                    className={styles.useAsInputButton}
                    data-active={usedAsInput}
                    disabled={usedAsInput}
                    title={usedAsInput ? rp.inUseHint : undefined}
                    onClick={handleUseAsInput}
                  >
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                      <path
                        d="M8 13V4M4.5 7.5L8 4l3.5 3.5"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                    {usedAsInput ? rp.inUseAsInput : rp.useAsInput}
                  </button>
                  {addedNotice && <p className={styles.addedNotice}>{rp.added}</p>}
                  {toggleError && <p className={styles.toggleError}>{toggleError}</p>}
                </div>
              </div>
              )}
            </>
          )}

          {!effectiveAssetId && pendingRunId && !pendingStatus && (
            <p className={styles.emptyState}>{rp.connecting}</p>
          )}

          {showRunDetail && pendingRunId && (
            <div className={styles.runDetailScroll}>
              <RunDetailContent
                key={pendingRunId}
                runId={pendingRunId}
                compact
                promptActions={(prompt) => (
                  <RunPromptActions prompt={prompt} currentPrompt={formState.prompt} insertPrompt={insertPrompt} />
                )}
              />
            </div>
          )}

          {!effectiveAssetId && pendingRunId && pendingStatus && !showRunDetail && (
            <>
              {(pendingStatus === 'queued' || pendingStatus === 'running') && (
                <div className={styles.pendingArea}>
                  {partials.length > 0 ? (
                    <>
                      <div className={styles.partialGrid}>
                        {/* 出力ごとに最新の1枚だけを出し、届くたびに差し替える。 */}
                        {latestPartialsPerOutput(partials).map((event) => (
                          <img
                            key={event.output_index ?? 0}
                            className={styles.partialThumb}
                            src={runPartialUrl(pendingRunId, event.index ?? 0)}
                            alt={fmt(rp.partialAlt, { index: event.partial_index ?? '' })}
                            draggable={false}
                          />
                        ))}
                      </div>
                      {progress && (
                        <p className={styles.pendingText}>
                          {progress.value ?? 0} / {progress.max ?? 0}
                        </p>
                      )}
                    </>
                  ) : (
                    <p className={styles.pendingText}>
                      {pendingStatus === 'running'
                        ? progress
                          ? fmt(rp.generatingWithProgress, { value: progress.value ?? 0, max: progress.max ?? 0 })
                          : rp.generating
                        : rp.queued}
                    </p>
                  )}
                  {pendingStatus === 'queued' && (
                    <button
                      type="button"
                      className={styles.cancelButton}
                      onClick={() => cancelMutation.mutate()}
                      disabled={cancelMutation.isPending}
                    >
                      {rp.cancel}
                    </button>
                  )}
                  {pendingStatus === 'running' && (
                    <div className={styles.progressTrack}>
                      {/* progress が来ないプロバイダー(OpenAI 等)は不定のスライドアニメーションのまま。
                          ComfyUI 等がステップ進捗を送ってきたら value/max に応じた実バーへ切り替える。 */}
                      <div
                        className={styles.progressBar}
                        data-determinate={progress ? 'true' : undefined}
                        style={progress ? { width: `${progressPercent(progress)}%` } : undefined}
                      />
                    </div>
                  )}
                </div>
              )}

              {pendingStatus === 'failed' && (
                <div className={styles.errorArea}>
                  <p className={styles.errorText}>
                    {describeError(pendingRunQuery.data?.error_code, pendingRunQuery.data?.error_message)}
                  </p>
                  {pendingRunQuery.data?.error_code && (
                    <p className={styles.errorCode}>{rp.errorCodePrefix}{pendingRunQuery.data.error_code}</p>
                  )}
                </div>
              )}

              {pendingStatus === 'canceled' && (
                <div className={styles.errorArea}>
                  <p className={styles.pendingText}>{rp.canceledMessage}</p>
                </div>
              )}
            </>
          )}

          {effectiveAssetId && !asset && (
            <p className={styles.emptyState}>
              {assetQuery.isError ? rp.assetLoadFailed : rp.loading}
            </p>
          )}

          {origin.kind === 'empty' && (
            <p className={styles.emptyState}>{rp.emptyState}</p>
          )}
        </div>
      )}

      <ConfirmDialog
        open={deleteConfirmOpen}
        message={rp.deleteConfirmMessage}
        previewImageUrl={asset ? assetUrl(asset.id, 'thumb') : undefined}
        previewDetail={asset ? `${asset.width} × ${asset.height}` : undefined}
        onConfirm={() => deleteMutation.mutate()}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </div>
  )
}

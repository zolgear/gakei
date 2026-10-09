/**
 * ログイン不要の共有のページ `/s/:token`(ADR-0029)。`main.tsx` が `AuthGate` とルーティングの
 * 外で描く(`/api/auth/me` に依存しない。App バーやサイドバーなど、ログイン前提の画面は出さない)。
 *
 * - 画像: 選んだ画像をパン/ズームで表示する(`AssetCanvas`)。原本を許す共有なら、拡大すると
 *   原本に差し替え、ダウンロードもできる。許さない共有はプレビュー(長辺 2048px)まで。共有が
 *   許していても、画像ごとの `allow_original` が false の画像(秘密に見える値を含む ComfyUI の
 *   Run の画像。2026-10-01 追記)は、許さない共有と同じくプレビューまで。
 * - 画像のタイトル、作成日時、その画像を作った Run のプロンプト(と最終プロンプト。ADR-0030)と
 *   パラメーター。
 * - 含まれる画像の一覧(2枚以上のとき)と、範囲が系列なら系列グラフ。
 * - Run の詳細(2026-09-30 追記): グラフの Run のノード(または画像の情報の「Generated 詳細を
 *   見る」)で、右のパネルを Run の詳細に切り替える。URL は `/s/:token/runs/:runId`(直リンクで
 *   開ける。画像の選択はこれまでどおり URL に書かない)。Edit の Run では「編集前と比較する」で
 *   ビューアの場所に比較(`CompareCanvas`)を出す。
 * - 系列グラフを広げる(2026-10-01 追記): 系列グラフの「広げる」で、グラフだけを全画面で出す。
 *   URL は `/s/:token/lineage`(直リンクで開ける。系列を含まない共有ではビューアのまま)。ノードを
 *   押すと全画面を閉じ、画像ならビューアでその画像、Run ならその Run の詳細を開く。
 * - 見つからない・取り消し済み・機能が無効・共有に無い Run は、区別せず「このリンクは無効です」を出す。
 *
 * ルーターの外なので、URL は `history.pushState` と `popstate` で自前で扱う。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getPublicShare, type PublicShareAsset, type PublicShareResponse } from '../../api/client'
import { publicShareAssetUrl } from '../../api/assetUrl'
import { GakeiMark } from '../../components/GakeiMark'
import { assetKindLabel, formatDateTime } from '../../lib/format'
import { useIsMobileViewport } from '../../lib/viewport'
import { fmt, useI18n } from '../../i18n'
import { AssetCanvas } from '../viewer/AssetCanvas'
import { CompareCanvas } from '../compare/CompareCanvas'
import { CompareModeSwitch } from '../compare/CompareModeSwitch'
import { resolveEffectiveMode, type CompareMode } from '../compare/compareState'
import { FinalPromptSection } from '../run-detail/FinalPromptSection'
import { baselineNegativePrompt } from '../run-detail/finalPrompt'
import { PublicLineageGraph } from './PublicLineageGraph'
import { PublicRunDetailPanel } from './PublicRunDetailPanel'
import {
  assetForRun,
  buildPublicRunDetail,
  compareAllowsOriginal,
  originalAvailability,
  publicCompareTargets,
  publicOutputIndex,
} from './publicRunDetail'
import {
  closeLineageView,
  lineageNodeTarget,
  openLineageView,
  publicShareHistoryState,
  publicShareViewFromLocation,
  publicShareViewPath,
  type PublicShareView,
} from './publicShareView'
import { showsLineage } from './shareScope'
import { PublicParams } from './PublicParams'
import styles from './PublicSharePage.module.css'

interface PublicSharePageProps {
  token: string
  /** 直リンク `/s/:token/runs/:runId` で開いたときの Run。 */
  initialRunId?: string | null
  /** 直リンク `/s/:token/lineage` で開いたか。 */
  initialLineage?: boolean
}

export function PublicSharePage({ token, initialRunId = null, initialLineage = false }: PublicSharePageProps) {
  const { t } = useI18n()
  const query = useQuery({
    queryKey: ['public-share', token],
    queryFn: () => getPublicShare(token),
    retry: false,
    refetchOnWindowFocus: false,
  })
  const [view, setView] = useState<PublicShareView>(() => ({ runId: initialRunId, lineage: initialLineage }))

  useEffect(() => {
    document.title = 'GAKEI'
  }, [])

  // ブラウザの戻る/進むで、Run の詳細・全画面の系列グラフの開閉を URL に合わせる。
  useEffect(() => {
    const onPopState = (event: PopStateEvent) => {
      const next = publicShareViewFromLocation(window.location.pathname, event.state)
      setView(next ?? { runId: null, lineage: false })
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const navigateView = useCallback(
    (next: PublicShareView, options: { replace?: boolean } = {}) => {
      const path = publicShareViewPath(token, next)
      const state = publicShareHistoryState(next)
      if (options.replace) window.history.replaceState(state, '', path)
      else if (window.location.pathname !== path) window.history.pushState(state, '', path)
      setView(next)
    },
    [token],
  )

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <GakeiMark size={22} />
        <span className={styles.brand}>GAKEI</span>
        {query.data && (
          <span className={styles.headerNote}>
            {t.publicShare.sharedBy} · {fmt(t.publicShare.images, { count: query.data.assets?.length ?? 0 })}
          </span>
        )}
      </header>
      {query.isLoading && <p className={styles.message}>{t.publicShare.loading}</p>}
      {query.isError && <InvalidLink />}
      {query.data && <PublicShareBody token={token} data={query.data} view={view} onNavigate={navigateView} />}
    </div>
  )
}

function InvalidLink() {
  const { t } = useI18n()
  return (
    <div className={styles.invalid}>
      <h1 className={styles.invalidTitle}>{t.publicShare.invalid}</h1>
      <p className={styles.invalidDetail}>{t.publicShare.invalidDetail}</p>
    </div>
  )
}

interface PublicShareBodyProps {
  token: string
  data: PublicShareResponse
  view: PublicShareView
  onNavigate: (view: PublicShareView, options?: { replace?: boolean }) => void
}

function PublicShareBody({ token, data, view, onNavigate }: PublicShareBodyProps) {
  const { t } = useI18n()
  const p = t.publicShare
  const isMobile = useIsMobileViewport()
  const assets = useMemo(() => data.assets ?? [], [data.assets])
  const runId = view.runId
  const hasLineage = showsLineage(data.scope)
  // 系列を含まない共有(「この1枚」)の `/lineage` は、ビューアで開く(URL も `/s/:token` に直す)。
  const lineageOpen = view.lineage && hasLineage
  const runDetail = useMemo(() => (runId ? buildPublicRunDetail(data, runId) : null), [data, runId])
  const onNavigateRun = useCallback(
    (next: string | null) => onNavigate({ runId: next, lineage: false }),
    [onNavigate],
  )
  useEffect(() => {
    if (view.lineage && !hasLineage) onNavigate(closeLineageView(view), { replace: true })
  }, [view, hasLineage, onNavigate])

  const [selectedId, setSelectedId] = useState<string>(
    () => (runDetail ? assetForRun(runDetail, null) : null) ?? data.root_asset_id,
  )
  const [comparing, setComparing] = useState(false)
  const [compareMode, setCompareMode] = useState<CompareMode | null>(null)

  // Run の詳細を開いた(戻る/進むも含む)ら、ビューアをその Run の出力に合わせ、比較は閉じる
  // (レンダー中に直前の値とのずれを補正する書き方。`CompareCanvas` と同じ)。
  const [shownRunId, setShownRunId] = useState(runId)
  if (shownRunId !== runId) {
    setShownRunId(runId)
    setComparing(false)
    if (runDetail) {
      const next = assetForRun(runDetail, selectedId)
      if (next && next !== selectedId) setSelectedId(next)
    }
  }

  // 利用者の操作で Run の詳細を開閉したら、パネルの先頭を見せる(パネルの中を先頭に戻し、狭い幅
  // ではページもパネルの先頭まで送る。グラフは下にあるので、押した位置のままだと中身が見えない)。
  // 最初の表示(直リンク)では動かさない。
  const sideRef = useRef<HTMLElement | null>(null)
  const scrolledRunIdRef = useRef(runId)
  useEffect(() => {
    if (scrolledRunIdRef.current === runId) return
    scrolledRunIdRef.current = runId
    const side = sideRef.current
    if (!side) return
    side.scrollTop = 0
    if (isMobile) side.scrollIntoView({ block: 'start' })
  }, [runId, isMobile])

  const thumbUrlFor = useCallback(
    (assetId: string) => publicShareAssetUrl(token, assetId, 'thumb'),
    [token],
  )
  const compareUrlFor = useCallback(
    (assetId: string, variant: 'preview' | 'original') => publicShareAssetUrl(token, assetId, variant),
    [token],
  )

  const selected: PublicShareAsset | undefined =
    assets.find((a) => a.id === selectedId) ?? assets.find((a) => a.id === data.root_asset_id) ?? assets[0]

  // 共有に無い Run の直リンク(失敗・取り消しの Run や範囲外の Run を含む)は、ほかと同じく無効。
  if (!selected || (runId !== null && runDetail === null)) {
    return <InvalidLink />
  }

  const run = selected.run_id ? (data.runs ?? []).find((r) => r.id === selected.run_id) : undefined
  // 一覧の画像(マスクは系列グラフにだけ出す。単独で見せる意味が薄いため)。
  const listed = assets.filter((a) => a.kind !== 'mask')
  const compareTargets = runDetail ? publicCompareTargets(runDetail, selected.id) : null
  const effectiveCompareMode = resolveEffectiveMode(compareMode, isMobile)
  // この画像の原本を出せるか(共有単位の許可と、画像ごとの判定。ADR-0029 4章)。
  const original = originalAvailability(data, selected)

  function selectAsset(assetId: string) {
    setComparing(false)
    setSelectedId(assetId)
  }

  // 画像のノード・一覧を選んだら、画像の情報に戻る(URL は `/s/:token`)。
  function showAsset(assetId: string) {
    selectAsset(assetId)
    if (runId !== null) onNavigateRun(null)
  }

  // 強調するノード: Run の詳細を開いているならその Run、そうでなければビューアの画像。
  const highlightedNodeId = runDetail ? runDetail.run.id : selected.id

  if (lineageOpen) {
    const selectFromFull = (id: string, type: 'asset' | 'run') => {
      const target = lineageNodeTarget({ id, type })
      if (!target) return
      if (target.assetId) selectAsset(target.assetId)
      onNavigate(target.view)
    }
    return (
      <PublicLineageFull
        data={data}
        highlightedNodeId={highlightedNodeId}
        thumbUrlFor={thumbUrlFor}
        onSelectAsset={(id) => selectFromFull(id, 'asset')}
        onSelectRun={(id) => selectFromFull(id, 'run')}
        onClose={() => onNavigate(closeLineageView(view))}
      />
    )
  }

  return (
    <main className={styles.main}>
      <section className={styles.viewer}>
        {comparing && compareTargets ? (
          <div className={styles.compare}>
            <div className={styles.compareHeader}>
              <h2 className={styles.compareTitle}>{t.compare.title}</h2>
              <div className={styles.compareActions}>
                {!isMobile && <CompareModeSwitch mode={effectiveCompareMode} onChange={setCompareMode} />}
                <button type="button" className={styles.compareEnd} onClick={() => setComparing(false)}>
                  {p.endCompare}
                </button>
              </div>
            </div>
            <div className={styles.compareCanvas}>
              <CompareCanvas
                before={compareTargets.before}
                after={compareTargets.after}
                mode={effectiveCompareMode}
                urlFor={compareUrlFor}
                allowOriginal={compareAllowsOriginal(data, compareTargets)}
              />
            </div>
          </div>
        ) : (
          <AssetCanvas
            asset={selected}
            urlFor={(variant) => publicShareAssetUrl(token, selected.id, variant)}
            allowOriginal={original === 'allowed'}
          />
        )}
      </section>

      <aside className={styles.side} ref={sideRef}>
        {runDetail ? (
          <PublicRunDetailPanel
            detail={runDetail}
            selectedAssetId={selected.id}
            thumbUrlFor={thumbUrlFor}
            onSelectAsset={selectAsset}
            onCompare={compareTargets ? () => setComparing(true) : undefined}
            onBack={() => onNavigateRun(null)}
          />
        ) : (
          <>
            <h1 className={styles.title} data-untitled={selected.title ? undefined : 'true'}>
              {selected.title ?? p.untitled}
            </h1>
            <dl className={styles.meta}>
              <dt>{p.created}</dt>
              <dd>{formatDateTime(selected.created_at)}</dd>
              <dt>{p.dimensions}</dt>
              <dd>
                {selected.width} × {selected.height} px
                {selected.kind !== 'generated' &&
                  assetKindLabel(selected.kind) &&
                  ` · ${assetKindLabel(selected.kind)}`}
              </dd>
              {run && (
                <>
                  <dt>{p.model}</dt>
                  <dd>{run.model || '-'}</dd>
                  <dt>{p.operation}</dt>
                  <dd>{p.operations[run.operation]}</dd>
                </>
              )}
            </dl>

            {run ? (
              <>
                <div className={styles.subheadingRow}>
                  <h2 className={styles.subheading}>{p.promptHeading}</h2>
                  <button type="button" className={styles.linkButton} onClick={() => onNavigateRun(run.id)}>
                    {p.viewRunDetail}
                  </button>
                </div>
                <p className={styles.prompt}>{run.prompt}</p>
                {/* 最終プロンプト(ADR-0030 4章)。閲覧専用なので「コピー」だけ(挿入・置き換えは出さない)。 */}
                <FinalPromptSection
                  className={styles.finalPrompt}
                  textOutputs={run.text_outputs}
                  outputIndex={publicOutputIndex(data, run.id, selected.id)}
                  prompt={run.prompt}
                  negativePrompt={baselineNegativePrompt(run.params)}
                  headingLevel="h2"
                  headingClassName={styles.subheading}
                />
                <PublicParams params={run.params} />
              </>
            ) : (
              <p className={styles.note}>{p.noRun}</p>
            )}
          </>
        )}

        {original === 'allowed' ? (
          <a
            className={styles.downloadButton}
            href={publicShareAssetUrl(token, selected.id, 'original', { download: true })}
          >
            {p.downloadOriginal}
          </a>
        ) : (
          <p className={styles.note}>
            {original === 'image' ? p.originalNotAllowedForImage : p.originalNotAllowed}
          </p>
        )}

        {!runDetail && listed.length > 1 && (
          <>
            <h2 className={styles.subheading}>{p.imageListLabel}</h2>
            <ul className={styles.thumbs}>
              {listed.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    className={styles.thumbButton}
                    aria-current={a.id === selected.id}
                    aria-label={a.title ?? p.untitled}
                    title={a.title ?? undefined}
                    onClick={() => showAsset(a.id)}
                  >
                    <img src={thumbUrlFor(a.id)} alt="" className={`${styles.thumbImg} checkerboard`} />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {hasLineage && (
          <>
            <h2 className={styles.subheading}>{p.lineageHeading}</h2>
            <PublicLineageGraph
              data={data}
              highlightedNodeId={highlightedNodeId}
              thumbUrlFor={thumbUrlFor}
              onSelectAsset={showAsset}
              onSelectRun={onNavigateRun}
              onExpand={() => onNavigate(openLineageView(view))}
            />
          </>
        )}
      </aside>
    </main>
  )
}

interface PublicLineageFullProps {
  data: PublicShareResponse
  highlightedNodeId: string
  thumbUrlFor: (assetId: string) => string
  onSelectAsset: (assetId: string) => void
  onSelectRun: (runId: string) => void
  onClose: () => void
}

/** 全画面の系列グラフ(`/s/:token/lineage`)。Esc でも「ビューアに戻る」と同じく閉じる。 */
function PublicLineageFull({ onClose, ...graphProps }: PublicLineageFullProps) {
  const { t } = useI18n()
  const p = t.publicShare
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <main className={styles.lineageFull}>
      <div className={styles.lineageFullHeader}>
        <button type="button" className={styles.backButton} onClick={onClose}>
          {p.backToViewer}
        </button>
        <h1 className={styles.lineageFullTitle}>{p.lineageHeading}</h1>
      </div>
      <PublicLineageGraph {...graphProps} variant="full" />
    </main>
  )
}

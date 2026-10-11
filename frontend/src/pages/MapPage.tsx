/**
 * `/map` マップ(ADR-0033 8章)。埋め込みの近さで画像を並べる。既定のタブはネットワーク
 * (2026-10-04 から)。
 *
 * - ネットワーク: 類似度がしきい値以上の組を辺にした、3 次元の力学モデル(`NetworkView`)。系列の
 *   辺も重ねられる。
 * - 地図: umap-js の 3 次元の配置を、回る立体として見る(`Umap3DView`。試作)。
 *
 * タブはこの 2 つ。別に「見せ方」(平面 / 立体)の切り替えがあり、どちらのタブにも効く
 * (タブ = 何を並べるか、見せ方 = どう見せるか)。既定は立体で、平面を選ぶとネットワークは
 * d3-force の 2 次元、地図は `UmapView`(UMAP 2 次元)になる。以前の `view=map` と覚えた値は
 * 地図のタブとして読む。
 *
 * 元データは `GET /api/embeddings/graph`。グループ・タグ(複数なら AND)・上限・近傍の数で絞り込める。タブ、
 * 絞り込み、ネットワークのしきい値と系列の辺の表示、選んだ画像は URL に置く(ビューアから戻った
 * ときに保つ。履歴を増やさないよう replace で書く)。タブ・上限・近傍の数・しきい値・系列の辺は
 * ブラウザにも覚え、URL に無ければ覚えた値を使う(`mapPrefs.ts`。2026-10-04)。選んだ画像を大きく見る
 * パネルの開閉もブラウザにだけ覚える(URL には置かない)。上限で切ったときは、絞り込みを促す。
 * 埋め込みが使えないときは App バーの入口を出さない。直接開いたときは使えない旨だけを出す。
 *
 * マップ内の意味検索(試作)は、タブのすぐ下の入力欄。打ち終わってからマップと同じ絞り込みで
 * `GET /api/embeddings/graph/scores` を呼び、Asset ID で突き合わせて「上位 N 件」を一致とする
 * (類似度の帯が狭いので絶対値のしきい値は使わない。`mapSearchHighlight.ts`)。検索の文章は URL
 * だけに置き、一致の件数と距離の取り方はブラウザにも覚える。配置の距離の取り方(試作)は、
 * 効く地図のタブでだけ選べる。
 */
import { useId, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { embeddingGraph, embeddingGraphScores } from '../api/client'
import { TagAutocomplete } from '../features/annotations/TagAutocomplete'
import { TagChip } from '../features/annotations/TagChip'
import { embeddingErrorKind, retryEmbeddingQuery } from '../features/embeddings/embeddingErrors'
import { useEmbeddingCapabilitiesState } from '../features/embeddings/useEmbeddingCapabilities'
import { lineagePairs, similarityEdges } from '../features/map/edges'
import {
  buildSearchHighlight,
  MAP_MATCH_COUNT_CHOICES,
} from '../features/map/mapSearchHighlight'
import { MAP_DISTANCE_METRICS, type MapDistanceMetric } from '../features/map/umapInput'
import {
  MAP_K_CHOICES,
  MAP_SHAPES,
  MAP_LIMIT_CHOICES,
  MAP_QUERY_MAX,
  MAP_THRESHOLD_MAX,
  MAP_THRESHOLD_MIN,
  MAP_THRESHOLD_STEP,
  activeFilterCount,
  buildMapSearchParams,
  isMapSettingsChange,
  clampThreshold,
  normalizeTagList,
  parseMapUrlState,
  type MapUrlState,
  type MapView,
} from '../features/map/mapUrlState'
import { loadMapPrefs, pickStoredFields, saveMapPrefs } from '../features/map/mapPrefs'
import { NetworkView } from '../features/map/NetworkView'
import { Umap3DView } from '../features/map/Umap3DView'
import { UmapView } from '../features/map/UmapView'
import { useDebouncedValue } from '../features/search/useDebouncedValue'
import { useAssetGroups } from '../features/stock/groups/assetGroupQueries'
import { fmt, useI18n } from '../i18n'
import styles from './MapPage.module.css'

const THRESHOLD_DEBOUNCE_MS = 200
/** 検索の文章を打ち終わるのを待つ時間。 */
const SEARCH_DEBOUNCE_MS = 300

export function MapPage() {
  const { t } = useI18n()
  const m = t.map
  const caps = useEmbeddingCapabilitiesState()
  const available = caps.embeddings !== null

  return (
    <div className={styles.page}>
      {caps.isLoading && <p className={styles.placeholder}>{m.loading}</p>}
      {!caps.isLoading && !available && (
        <>
          <h1 className={styles.title}>{m.title}</h1>
          <p className={styles.placeholder}>{t.embeddings.unavailable}</p>
        </>
      )}
      {available && <MapBody />}
    </div>
  )
}

function MapBody() {
  const { t } = useI18n()
  const m = t.map
  const [searchParams, setSearchParams] = useSearchParams()
  // URL の更新は少し遅れて反映されるので、押した値を先に画面へ出す(チェックボックスが一瞬
  // 元に戻って見えないように)。URL が変わったら、URL の値に任せる。
  const [pending, setPending] = useState<{ base: string; patch: Partial<MapUrlState> } | null>(null)
  // 覚えた値は開いたときに1回だけ読み、変えたらここと localStorage の両方を書き換える
  // (既定に戻した値は URL から省くので、覚えた値も同時に変えないと戻らない)。
  const [storedPrefs, setStoredPrefs] = useState(loadMapPrefs)
  const urlKey = searchParams.toString()
  const urlState = parseMapUrlState(searchParams, storedPrefs)
  const state: MapUrlState = pending && pending.base === urlKey ? { ...urlState, ...pending.patch } : urlState
  const [filtersOpen, setFiltersOpen] = useState(false)
  /** 画面の設定を変えた回数(開いたときの最初の回転を止めるのに使う)。 */
  const [settingsVersion, setSettingsVersion] = useState(0)
  const threshold = state.threshold
  const debouncedThreshold = useDebouncedValue(threshold, THRESHOLD_DEBOUNCE_MS)
  const filtersId = useId()
  const thresholdId = useId()

  // スライダーを動かすたびに呼ばれるので、履歴を増やさないよう replace で書く。
  function update(patch: Partial<MapUrlState>) {
    if (isMapSettingsChange(patch)) setSettingsVersion((v) => v + 1)
    setPending((prev) => ({ base: urlKey, patch: { ...(prev && prev.base === urlKey ? prev.patch : {}), ...patch } }))
    if (Object.keys(pickStoredFields(patch)).length > 0) setStoredPrefs(saveMapPrefs(storedPrefs, patch))
    setSearchParams(buildMapSearchParams({ ...state, ...patch }), { replace: true })
  }

  function selectId(selectedId: string | null) {
    if (selectedId !== state.selectedId) update({ selectedId })
  }

  // パネルの開閉は URL に置かず、覚えた値だけを書き換える。
  const previewOpen = storedPrefs.previewOpen ?? false
  function setPreviewOpen(open: boolean) {
    if (open !== previewOpen) setStoredPrefs(saveMapPrefs(storedPrefs, { previewOpen: open }))
  }
  const preview = { open: previewOpen, onOpenChange: setPreviewOpen }

  /**
   * 開いたときに一度だけ回すか(どちらのタブも立体)。URL には置かず、覚えた値だけを見る
   * (既定は回す)。
   * 設定を変えたら回転を止め、以後は回し直さない(タブを切り替えて戻ってきても回さない)。
   */
  const introSpin = {
    enabled: (storedPrefs.autoRotate3d ?? true) && settingsVersion === 0,
    settingsVersion,
  }

  // 座標のラインの重ね表示。URL には置かず、覚えた値だけを見る(既定は出さない)。
  const axesShown = storedPrefs.axes3d ?? false
  const axes = {
    shown: axesShown,
    onShownChange: (shown: boolean) => {
      if (shown !== axesShown) setStoredPrefs(saveMapPrefs(storedPrefs, { axes3d: shown }))
    },
  }

  const query = useQuery({
    queryKey: ['embeddings', 'graph', state.k, state.limit, state.groupId, state.tags],
    queryFn: () =>
      embeddingGraph({
        k: state.k,
        limit: state.limit,
        group_id: state.groupId ?? undefined,
        tag: state.tags.length > 0 ? state.tags : undefined,
        include_lineage: true,
      }),
    retry: retryEmbeddingQuery,
    placeholderData: keepPreviousData,
    // 取り直すと配置が動いて見えるので、焦点が戻ったくらいでは取り直さない。
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })
  const graph = query.data

  // マップ内の意味検索(試作)。文章を打ち終わってから、マップと同じ絞り込みで類似度を取る。
  const trimmedQuery = state.query.trim()
  const debouncedQuery = useDebouncedValue(trimmedQuery, SEARCH_DEBOUNCE_MS)
  const scoresQuery = useQuery({
    queryKey: ['embeddings', 'graph', 'scores', debouncedQuery, state.limit, state.groupId, state.tags],
    queryFn: () =>
      embeddingGraphScores({
        q: debouncedQuery,
        limit: state.limit,
        group_id: state.groupId ?? undefined,
        tag: state.tags.length > 0 ? state.tags : undefined,
      }),
    enabled: debouncedQuery !== '',
    retry: retryEmbeddingQuery,
    // 前の文章の結果をそのまま見せると、別の文章の一致を出してしまうので持ち越さない。
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })
  // 一致は Asset ID で突き合わせ、順位で決める(類似度の帯が狭いので絶対値のしきい値は使わない)。
  const nodeIds = useMemo(() => (graph?.nodes ?? []).map((node) => node.id), [graph])
  const scoresData = scoresQuery.data
  // 一致は数千件でも軽いので、毎回そのまま組む(`state` は押した値を先に出すために作り直す
  // オブジェクトなので、useMemo の依存に入れると React Compiler の最適化を止めてしまう)。
  const searchHighlight =
    debouncedQuery !== '' && scoresData
      ? buildSearchHighlight(nodeIds, scoresData.ids ?? [], scoresData.scores ?? [], state.matchCount, debouncedQuery)
      : null

  const edgeResult = useMemo(
    () =>
      graph
        ? similarityEdges(graph.neighbor_indices ?? [], graph.neighbor_similarities ?? [], debouncedThreshold)
        : { edges: [], total: 0 },
    [graph, debouncedThreshold],
  )
  const lineage = useMemo(
    () => (graph ? lineagePairs(graph.lineage_edges, graph.nodes?.length ?? 0) : []),
    [graph],
  )

  const nodeCount = graph?.nodes?.length ?? 0
  const filterCount = activeFilterCount(state)
  const errorKind = embeddingErrorKind(query.error)

  return (
    <>
      <header className={styles.toolbar}>
        <h1 className={styles.title}>{m.title}</h1>
        <div className={styles.tabs} role="tablist" aria-label={m.viewLabel}>
          {(['network', 'umap3d'] as MapView[]).map((view) => (
            <button
              key={view}
              type="button"
              role="tab"
              aria-selected={state.view === view}
              className={styles.tab}
              data-active={state.view === view}
              onClick={() => update({ view })}
            >
              {view === 'umap3d' ? m.tabMap : m.tabNetwork}
            </button>
          ))}
        </div>
        <div className={styles.tabs} role="group" aria-label={m.shapeLabel}>
          {MAP_SHAPES.map((shape) => (
            <button
              key={shape}
              type="button"
              aria-pressed={state.shape === shape}
              className={styles.tab}
              data-active={state.shape === shape}
              onClick={() => update({ shape })}
            >
              {m.shapeOption[shape]}
            </button>
          ))}
        </div>
        <button
          type="button"
          className={styles.filtersToggle}
          aria-expanded={filtersOpen}
          aria-controls={filtersId}
          onClick={() => setFiltersOpen((v) => !v)}
        >
          {filterCount > 0 ? fmt(m.filtersWithCount, { count: filterCount }) : m.filters}
        </button>
      </header>

      <div className={styles.searchBar} role="search">
        <input
          type="search"
          className={styles.searchInput}
          value={state.query}
          maxLength={MAP_QUERY_MAX}
          placeholder={m.searchPlaceholder}
          aria-label={m.searchLabel}
          onChange={(e) => update({ query: e.target.value })}
          onKeyDown={(e) => {
            // 入力欄の Esc は検索語を消すだけ(ビューの Esc の扱いは変えない)。
            if (e.key === 'Escape') {
              e.preventDefault()
              update({ query: '' })
            }
          }}
        />
        {trimmedQuery !== '' && (
          <label className={styles.inlineField}>
            <span className={styles.fieldLabel}>{m.matchCountLabel}</span>
            <select
              className={styles.select}
              value={state.matchCount}
              onChange={(e) => update({ matchCount: Number(e.target.value) })}
            >
              {MAP_MATCH_COUNT_CHOICES.map((value) => (
                <option key={value} value={value}>
                  {fmt(m.matchCountOption, { count: value })}
                </option>
              ))}
            </select>
          </label>
        )}
        <SearchStatus
          query={trimmedQuery}
          running={scoresQuery.isFetching}
          error={scoresQuery.isError ? scoresQuery.error : null}
          matches={searchHighlight?.ranked.length ?? null}
        />
      </div>

      <div id={filtersId} className={styles.filters} data-open={filtersOpen}>
        <GroupFilter value={state.groupId} onChange={(groupId) => update({ groupId })} />
        <TagFilter value={state.tags} onChange={(tags) => update({ tags })} />
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{m.limitLabel}</span>
          <select
            className={styles.select}
            value={state.limit}
            onChange={(e) => update({ limit: Number(e.target.value) })}
          >
            {MAP_LIMIT_CHOICES.map((value) => (
              <option key={value} value={value}>
                {fmt(m.limitOption, { count: value })}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field} title={m.kHelp}>
          <span className={styles.fieldLabel}>{m.kLabel}</span>
          <select className={styles.select} value={state.k} onChange={(e) => update({ k: Number(e.target.value) })}>
            {MAP_K_CHOICES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state.view === 'umap3d' && (
        <div className={styles.networkControls}>
          <label className={styles.inlineField} title={m.metricHelp}>
            <span className={styles.fieldLabel}>{m.metricLabel}</span>
            <select
              className={styles.select}
              value={state.metric}
              onChange={(e) => update({ metric: e.target.value as MapDistanceMetric })}
            >
              {MAP_DISTANCE_METRICS.map((value) => (
                <option key={value} value={value}>
                  {m.metricOption[value]}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {state.view === 'network' && (
        <div className={styles.networkControls}>
          <label className={styles.thresholdLabel} htmlFor={thresholdId}>
            {m.thresholdLabel}
          </label>
          <input
            id={thresholdId}
            type="range"
            className={styles.slider}
            min={MAP_THRESHOLD_MIN}
            max={MAP_THRESHOLD_MAX}
            step={MAP_THRESHOLD_STEP}
            value={threshold}
            onChange={(e) => update({ threshold: clampThreshold(Number(e.target.value)) })}
          />
          <span className={styles.thresholdValue}>{threshold.toFixed(2)}</span>
          {graph && (
            <span className={styles.edgeCount}>
              {edgeResult.total > edgeResult.edges.length
                ? fmt(m.edgeCountCapped, { count: edgeResult.edges.length, total: edgeResult.total })
                : fmt(m.edgeCount, { count: edgeResult.edges.length })}
            </span>
          )}
          <label className={styles.checkbox}>
            <input
              type="checkbox"
              checked={state.showLineage}
              onChange={(e) => update({ showLineage: e.target.checked })}
            />
            {/* canvas の系列の辺と同じ見た目(破線 + 子の側の塗った矢じり)。 */}
            <svg className={styles.lineageSwatch} width="26" height="10" viewBox="0 0 26 10" aria-hidden="true">
              <path d="M1 5H18" stroke="var(--color-edit)" strokeWidth="2" strokeDasharray="5 4" />
              <path d="M25 5L18 1.5V8.5Z" fill="var(--color-edit)" />
            </svg>
            {m.showLineage}
          </label>
        </div>
      )}

      {graph?.truncated && (
        <p className={styles.notice}>
          {fmt(state.limit < MAP_LIMIT_CHOICES[MAP_LIMIT_CHOICES.length - 1] ? m.truncated : m.truncatedAtMax, {
            total: graph.total,
            count: nodeCount,
          })}
        </p>
      )}

      <div className={styles.stage} data-fetching={query.isFetching && !query.isLoading}>
        {query.isLoading && <p className={styles.placeholder}>{m.loading}</p>}
        {query.isError && errorKind === 'unavailable' && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
        {query.isError && errorKind !== 'unavailable' && (
          <div className={styles.errorBox}>
            <p className={styles.error}>{m.loadFailed}</p>
            <button type="button" className={styles.retry} onClick={() => void query.refetch()}>
              {t.common.retry}
            </button>
          </div>
        )}
        {graph && !query.isError && nodeCount === 0 && (
          <p className={styles.placeholder}>{filterCount > 0 ? m.emptyFiltered : m.empty}</p>
        )}
        {graph && !query.isError && nodeCount > 0 && state.view === 'umap3d' && state.shape === 'flat' && (
          <UmapView
            graph={graph}
            selectedId={state.selectedId}
            onSelectId={selectId}
            preview={preview}
            metric={state.metric}
            search={searchHighlight}
          />
        )}
        {graph && !query.isError && nodeCount > 0 && state.view === 'umap3d' && state.shape === 'orbit' && (
          <Umap3DView
            graph={graph}
            selectedId={state.selectedId}
            onSelectId={selectId}
            preview={preview}
            introSpin={introSpin}
            axes={axes}
            metric={state.metric}
            search={searchHighlight}
          />
        )}
        {graph && !query.isError && nodeCount > 0 && state.view === 'network' && (
          <NetworkView
            graph={graph}
            edges={edgeResult.edges}
            threshold={debouncedThreshold}
            lineageEdges={state.showLineage ? lineage : null}
            selectedId={state.selectedId}
            onSelectId={selectId}
            shape={state.shape}
            preview={preview}
            introSpin={introSpin}
            axes={axes}
            search={searchHighlight}
          />
        )}
      </div>
    </>
  )
}

/** 検索の入力中・失敗・一致の件数を小さく出す。 */
function SearchStatus({
  query,
  running,
  error,
  matches,
}: {
  query: string
  running: boolean
  error: unknown
  matches: number | null
}) {
  const { t } = useI18n()
  const m = t.map
  if (query === '') return null
  if (error) {
    const kind = embeddingErrorKind(error)
    return (
      <span className={styles.searchStatus} data-tone="error">
        {kind === 'unavailable' ? t.embeddings.unavailable : m.searchFailed}
      </span>
    )
  }
  if (running) {
    return (
      <span className={styles.searchStatus} role="status">
        {m.searchRunning}
      </span>
    )
  }
  if (matches === null) return null
  return (
    <span className={styles.searchStatus} role="status">
      {matches > 0 ? fmt(m.searchMatches, { count: matches }) : m.searchNoMatch}
    </span>
  )
}

function GroupFilter({ value, onChange }: { value: string | null; onChange: (groupId: string | null) => void }) {
  const { t } = useI18n()
  const m = t.map
  const groups = useAssetGroups()
  const items = groups.data?.items ?? []
  return (
    <label className={styles.field}>
      <span className={styles.fieldLabel}>{m.groupLabel}</span>
      <select className={styles.select} value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">{m.groupAll}</option>
        {items.map((group) => (
          <option key={group.id} value={group.id}>
            {group.name}
          </option>
        ))}
        {/* URL のグループが一覧に無い(消された等)ときも、選んでいる状態を出す。 */}
        {value && groups.isSuccess && !items.some((g) => g.id === value) && (
          <option value={value}>{m.groupUnknown}</option>
        )}
      </select>
    </label>
  )
}

/** タグの絞り込み。複数を選べて、すべてが付いた画像だけを並べる(AND)。 */
function TagFilter({ value, onChange }: { value: string[]; onChange: (tags: string[]) => void }) {
  const { t } = useI18n()
  const m = t.map
  const f = t.stock.tagFilter
  const exclude = value.map((name) => ({ name }))
  const placeholder = value.length > 0 ? m.tagAddMore : f.placeholder
  return (
    <div className={styles.field} role="group" aria-label={m.tagLabel} title={m.tagHelp}>
      <span className={styles.fieldLabel}>{m.tagLabel}</span>
      <div className={styles.tagFilterBox}>
        {value.length > 0 && (
          <div className={styles.tagChipRow}>
            {value.map((name) => (
              <TagChip
                key={name}
                name={name}
                source="user"
                onRemove={() => onChange(value.filter((v) => v !== name))}
                removeLabel={fmt(f.clear, { name })}
              />
            ))}
          </div>
        )}
        <div className={styles.tagInput}>
          <TagAutocomplete
            placeholder={placeholder}
            ariaLabel={placeholder}
            suggestionsLabel={f.suggestions}
            exclude={exclude}
            onPick={(name) => {
              const next = normalizeTagList([...value, name])
              if (next.length === value.length) return false
              onChange(next)
            }}
          />
        </div>
      </div>
    </div>
  )
}

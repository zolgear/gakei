/**
 * `/map` マップ(ADR-0033 8章)。埋め込みの近さで画像を並べる。タブは2つ。
 *
 * - 地図: umap-js の 2D 配置(`UmapView`)。
 * - ネットワーク: 類似度がしきい値以上の組を辺にした力学モデル(`NetworkView`)。系列の辺も重ねられる。
 *
 * 元データは `GET /api/embeddings/graph`。グループ・タグ・上限・近傍の数で絞り込める。タブ、
 * 絞り込み、ネットワークのしきい値と系列の辺の表示、選んだ画像は URL に置く(ビューアから戻った
 * ときに保つ。履歴を増やさないよう replace で書く)。上限で切ったときは、絞り込みを促す。
 * 埋め込みが使えないときはアイコンレールの入口を出さない。直接開いたときは使えない旨だけを出す。
 */
import { useId, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { embeddingGraph } from '../api/client'
import { TagAutocomplete } from '../features/annotations/TagAutocomplete'
import { TagChip } from '../features/annotations/TagChip'
import { normalizeTagName } from '../features/annotations/tagInput'
import { embeddingErrorKind, retryEmbeddingQuery } from '../features/embeddings/embeddingErrors'
import { useEmbeddingCapabilitiesState } from '../features/embeddings/useEmbeddingCapabilities'
import { lineagePairs, similarityEdges } from '../features/map/edges'
import {
  MAP_K_CHOICES,
  MAP_LIMIT_CHOICES,
  MAP_THRESHOLD_MAX,
  MAP_THRESHOLD_MIN,
  MAP_THRESHOLD_STEP,
  activeFilterCount,
  buildMapSearchParams,
  clampThreshold,
  parseMapUrlState,
  type MapUrlState,
  type MapView,
} from '../features/map/mapUrlState'
import { NetworkView } from '../features/map/NetworkView'
import { UmapView } from '../features/map/UmapView'
import { useDebouncedValue } from '../features/search/useDebouncedValue'
import { useAssetGroups } from '../features/stock/groups/assetGroupQueries'
import { fmt, useI18n } from '../i18n'
import styles from './MapPage.module.css'

const THRESHOLD_DEBOUNCE_MS = 200

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
  const urlKey = searchParams.toString()
  const urlState = parseMapUrlState(searchParams)
  const state: MapUrlState = pending && pending.base === urlKey ? { ...urlState, ...pending.patch } : urlState
  const [filtersOpen, setFiltersOpen] = useState(false)
  const threshold = state.threshold
  const debouncedThreshold = useDebouncedValue(threshold, THRESHOLD_DEBOUNCE_MS)
  const filtersId = useId()
  const thresholdId = useId()

  // スライダーを動かすたびに呼ばれるので、履歴を増やさないよう replace で書く。
  function update(patch: Partial<MapUrlState>) {
    setPending((prev) => ({ base: urlKey, patch: { ...(prev && prev.base === urlKey ? prev.patch : {}), ...patch } }))
    setSearchParams(buildMapSearchParams({ ...state, ...patch }), { replace: true })
  }

  function selectId(selectedId: string | null) {
    if (selectedId !== state.selectedId) update({ selectedId })
  }

  const query = useQuery({
    queryKey: ['embeddings', 'graph', state.k, state.limit, state.groupId, state.tag],
    queryFn: () =>
      embeddingGraph({
        k: state.k,
        limit: state.limit,
        group_id: state.groupId ?? undefined,
        tag: state.tag ?? undefined,
        include_lineage: true,
      }),
    retry: retryEmbeddingQuery,
    placeholderData: keepPreviousData,
    // 取り直すと配置が動いて見えるので、焦点が戻ったくらいでは取り直さない。
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })
  const graph = query.data

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
          {(['umap', 'network'] as MapView[]).map((view) => (
            <button
              key={view}
              type="button"
              role="tab"
              aria-selected={state.view === view}
              className={styles.tab}
              data-active={state.view === view}
              onClick={() => update({ view })}
            >
              {view === 'umap' ? m.tabMap : m.tabNetwork}
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

      <div id={filtersId} className={styles.filters} data-open={filtersOpen}>
        <GroupFilter value={state.groupId} onChange={(groupId) => update({ groupId })} />
        <TagFilter value={state.tag} onChange={(tag) => update({ tag })} />
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
            <span className={styles.lineageSwatch} aria-hidden="true" />
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
        {graph && !query.isError && nodeCount > 0 && state.view === 'umap' && (
          <UmapView graph={graph} selectedId={state.selectedId} onSelectId={selectId} />
        )}
        {graph && !query.isError && nodeCount > 0 && state.view === 'network' && (
          <NetworkView
            graph={graph}
            edges={edgeResult.edges}
            threshold={debouncedThreshold}
            lineageEdges={state.showLineage ? lineage : null}
            selectedId={state.selectedId}
            onSelectId={selectId}
          />
        )}
      </div>
    </>
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

function TagFilter({ value, onChange }: { value: string | null; onChange: (tag: string | null) => void }) {
  const { t } = useI18n()
  const m = t.map
  const f = t.stock.tagFilter
  return (
    <div className={styles.field} role="group" aria-label={m.tagLabel}>
      <span className={styles.fieldLabel}>{m.tagLabel}</span>
      {value ? (
        <div className={styles.tagChipRow}>
          <TagChip name={value} source="user" onRemove={() => onChange(null)} removeLabel={fmt(f.clear, { name: value })} />
        </div>
      ) : (
        <div className={styles.tagInput}>
          <TagAutocomplete
            placeholder={f.placeholder}
            ariaLabel={f.placeholder}
            suggestionsLabel={f.suggestions}
            onPick={(name) => onChange(normalizeTagName(name) || null)}
          />
        </div>
      )}
    </div>
  )
}

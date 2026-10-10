/**
 * `/stock/duplicates` 重複の候補(ADR-0033 8章、6章、12章)。ストックのパネルからたどる。
 *
 * - 似た画像のグループ(`GET /api/embeddings/duplicates`)を、グループごとにサムネイルで並べる。
 *   各画像には大きさ、日時、種類、類似度を添える。グループの並びはサーバーの順(枚数の多い順、次に
 *   類似度の高い順)のまま並べ替えない。グループの中は古い順で、先頭は「最初」の画像。
 * - しきい値はスライダーで変えられる。初期値は管理者設定の値(`duplicate_threshold`)で、ページの中で
 *   変えても保存しない。動かしている間は少し待ってから取り直す。
 * - 比べる: 2枚のグループはその2枚、3枚以上のグループは2枚を選んで、比較の表示(`CompareCanvas`)で
 *   見比べる。左が古い方、右が新しい方。解像度が違っても同じ大きさに揃えて重ねる(`frameBasis='larger'`)。
 * - 削除: 確認してから既存の論理削除(`DELETE /api/assets/{id}`)。トーストの「元に戻す」で戻せる。
 *   重複の自動削除は作らない(ADR-0033 1章)。
 * 埋め込みが使えないときは、ストックの入口を出さない。直接開いたときは使えない旨だけを出す。
 */
import { useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import {
  ApiError,
  deleteAsset,
  embeddingDuplicates,
  getEmbeddingSettings,
  restoreAsset,
  type DuplicateAsset,
  type DuplicateGroup,
} from '../api/client'
import { assetUrl } from '../api/assetUrl'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Modal } from '../components/Modal'
import { ToastHost, useToast } from '../components/Toast'
import { CompareCanvas } from '../features/compare/CompareCanvas'
import { CompareModeSwitch } from '../features/compare/CompareModeSwitch'
import { resolveEffectiveMode, type CompareMode } from '../features/compare/compareState'
import {
  DUPLICATE_THRESHOLD_MAX,
  DUPLICATE_THRESHOLD_MIN,
  DUPLICATE_THRESHOLD_STEP,
  clampThreshold,
  compareTargets,
  formatCompactDateTime,
  formatScore,
  formatThreshold,
  orderOldestFirst,
  toggleCompareSelection,
} from '../features/embeddings/duplicates'
import { embeddingErrorKind, retryEmbeddingQuery } from '../features/embeddings/embeddingErrors'
import { useEmbeddingCapabilitiesState } from '../features/embeddings/useEmbeddingCapabilities'
import { useDebouncedValue } from '../features/search/useDebouncedValue'
import { EMBEDDING_SETTINGS_QUERY_KEY } from '../features/settings/queryKeys'
import { invalidateAssetGroupQueries } from '../features/stock/groups/assetGroupQueries'
import { fmt, intlLocale, useI18n } from '../i18n'
import { assetKindLabel, formatBytes, formatDateTime } from '../lib/format'
import { useBackNavigate } from '../lib/useBackNavigate'
import { useIsMobileViewport } from '../lib/viewport'
import styles from './DuplicatesPage.module.css'
import { focalStyle } from '../lib/focalPoint'

/** スライダーを動かしてから取り直すまでの待ち(ミリ秒)。 */
const THRESHOLD_DEBOUNCE_MS = 350

export function DuplicatesPage() {
  const { t } = useI18n()
  const m = t.duplicates
  const goBack = useBackNavigate('/')
  const caps = useEmbeddingCapabilitiesState()
  const available = caps.embeddings !== null

  const settingsQuery = useQuery({
    queryKey: EMBEDDING_SETTINGS_QUERY_KEY,
    queryFn: getEmbeddingSettings,
    enabled: available,
  })
  const defaultThreshold = settingsQuery.data?.duplicate_threshold ?? null

  return (
    <div className={styles.page}>
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.common.back}
      </button>
      <header className={styles.header}>
        <h1 className={styles.title}>{m.title}</h1>
        <p className={styles.intro}>{m.intro}</p>
      </header>

      {caps.isLoading && <p className={styles.placeholder}>{m.loading}</p>}
      {!caps.isLoading && !available && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
      {available && settingsQuery.isLoading && <p className={styles.placeholder}>{m.loading}</p>}
      {available && settingsQuery.isError && <p className={styles.error}>{m.loadFailed}</p>}
      {available && defaultThreshold !== null && <DuplicatesBody defaultThreshold={defaultThreshold} />}
    </div>
  )
}

function DuplicatesBody({ defaultThreshold }: { defaultThreshold: number }) {
  const { t } = useI18n()
  const m = t.duplicates
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const isMobile = useIsMobileViewport()
  // null なら管理者設定の値。ページの中で変えても保存しない。
  const [override, setOverride] = useState<number | null>(null)
  const threshold = override ?? defaultThreshold
  const debouncedThreshold = useDebouncedValue(threshold, THRESHOLD_DEBOUNCE_MS)
  const [selected, setSelected] = useState<string[]>([])
  const [comparing, setComparing] = useState<[DuplicateAsset, DuplicateAsset] | null>(null)
  const [compareMode, setCompareMode] = useState<CompareMode | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<DuplicateAsset | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['embeddings', 'duplicates', debouncedThreshold],
    queryFn: () => embeddingDuplicates({ threshold: debouncedThreshold }),
    retry: retryEmbeddingQuery,
    // しきい値を動かしている間も、前の結果を出したままにする(ちらつかせない)。
    placeholderData: keepPreviousData,
  })

  function invalidateAfterChange(assetId: string) {
    void queryClient.invalidateQueries({ queryKey: ['embeddings', 'duplicates'] })
    invalidateAssetGroupQueries(queryClient, [assetId])
  }

  const restoreMutation = useMutation({
    mutationFn: (assetId: string) => restoreAsset(assetId),
    onSuccess: (_data, assetId) => invalidateAfterChange(assetId),
    onError: (err: unknown) => setMessage(err instanceof ApiError ? err.message : m.restoreFailed),
  })

  const deleteMutation = useMutation({
    mutationFn: (assetId: string) => deleteAsset(assetId),
    onSuccess: (_data, assetId) => {
      setDeleteTarget(null)
      setMessage(null)
      setSelected((prev) => prev.filter((id) => id !== assetId))
      invalidateAfterChange(assetId)
      toast.show({
        message: m.deletedToast,
        actionLabel: m.undo,
        onAction: () => restoreMutation.mutate(assetId),
      })
    },
    onError: (err: unknown) => {
      setDeleteTarget(null)
      setMessage(err instanceof ApiError ? err.message : m.deleteFailed)
    },
  })

  const data = query.data
  const groups = data?.groups ?? []
  const errorKind = embeddingErrorKind(query.error)

  function openCompare(group: DuplicateGroup) {
    const pair = compareTargets(
      group.assets.map((a) => a.id),
      selected,
    )
    if (!pair) return
    const a = group.assets.find((x) => x.id === pair[0])
    const b = group.assets.find((x) => x.id === pair[1])
    if (a && b) setComparing(orderOldestFirst(a, b))
  }

  return (
    <>
      <div className={styles.controls}>
        <label className={styles.thresholdLabel} htmlFor="gakei-duplicates-threshold">
          {m.thresholdLabel}
        </label>
        <div className={styles.thresholdRow}>
          <input
            id="gakei-duplicates-threshold"
            type="range"
            className={styles.slider}
            min={DUPLICATE_THRESHOLD_MIN}
            max={DUPLICATE_THRESHOLD_MAX}
            step={DUPLICATE_THRESHOLD_STEP}
            value={threshold}
            aria-describedby="gakei-duplicates-threshold-help"
            onChange={(e) => {
              const next = clampThreshold(Number(e.target.value))
              if (next !== null) setOverride(next)
            }}
          />
          <output className={styles.thresholdValue} htmlFor="gakei-duplicates-threshold">
            {formatThreshold(threshold)}
          </output>
          {override !== null && override !== defaultThreshold && (
            <button type="button" className={styles.linkButton} onClick={() => setOverride(null)}>
              {m.resetThreshold}
            </button>
          )}
        </div>
        <p id="gakei-duplicates-threshold-help" className={styles.help}>
          {m.thresholdHelp}
        </p>
      </div>

      {message && <p className={styles.error}>{message}</p>}
      {query.isLoading && <p className={styles.placeholder}>{m.loading}</p>}
      {errorKind === 'unavailable' && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
      {query.isError && errorKind === null && <p className={styles.error}>{m.loadFailed}</p>}

      {data && (
        <div className={styles.summary} data-stale={query.isPlaceholderData}>
          <p className={styles.summaryText}>
            {fmt(m.summary, { count: groups.length, groups: groups.length, scanned: data.scanned })}
          </p>
          {data.truncated && <p className={styles.help}>{fmt(m.truncatedNote, { scanned: data.scanned })}</p>}
          {data.groups_truncated && <p className={styles.help}>{m.groupsTruncatedNote}</p>}
        </div>
      )}
      {data && groups.length === 0 && <p className={styles.placeholder}>{m.empty}</p>}

      <ol className={styles.groupList} data-stale={query.isPlaceholderData}>
        {groups.map((group, index) => {
          const ids = group.assets.map((a) => a.id)
          const pair = compareTargets(ids, selected)
          const selectable = group.assets.length > 2
          return (
            <li key={ids.join(',')} className={styles.group}>
              <div className={styles.groupHeader}>
                <h2 className={styles.groupHeading}>
                  {fmt(m.groupHeading, { index: index + 1 })}
                  <span className={styles.groupMeta}>
                    {fmt(m.groupCount, { count: group.assets.length })} ·{' '}
                    {fmt(m.groupMaxScore, { score: formatScore(group.max_score) })}
                  </span>
                </h2>
                <div className={styles.groupActions}>
                  {selectable && !pair && <span className={styles.help}>{m.compareHint}</span>}
                  <button
                    type="button"
                    className={styles.button}
                    disabled={!pair}
                    onClick={() => openCompare(group)}
                  >
                    {m.compare}
                  </button>
                </div>
              </div>
              <ul className={styles.tiles}>
                {group.assets.map((asset, i) => (
                  <DuplicateTile
                    key={asset.id}
                    asset={asset}
                    first={i === 0}
                    selectable={selectable}
                    selected={selected.includes(asset.id)}
                    onToggleSelect={() => setSelected((prev) => toggleCompareSelection(prev, asset.id))}
                    onOpen={() => navigate(`/assets/${asset.id}`)}
                    onDelete={() => setDeleteTarget(asset)}
                    deleting={deleteMutation.isPending}
                  />
                ))}
              </ul>
            </li>
          )
        })}
      </ol>

      <Modal open={comparing !== null} title={m.compareTitle} size="large" onClose={() => setComparing(null)}>
        {comparing && (
          <div className={styles.compareBody}>
            {!isMobile && (
              <div className={styles.compareToolbar}>
                <CompareModeSwitch mode={resolveEffectiveMode(compareMode, isMobile)} onChange={setCompareMode} />
              </div>
            )}
            <div className={styles.compareCanvas}>
              <CompareCanvas
                before={comparing[0]}
                after={comparing[1]}
                mode={resolveEffectiveMode(compareMode, isMobile)}
                beforeLabel={fmt(m.compareOlder, { width: comparing[0].width, height: comparing[0].height })}
                afterLabel={fmt(m.compareNewer, { width: comparing[1].width, height: comparing[1].height })}
                frameBasis="larger"
              />
            </div>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={deleteTarget !== null}
        message={m.deleteConfirmMessage}
        previewImageUrl={deleteTarget ? assetUrl(deleteTarget.id, 'thumb') : undefined}
        previewFocalPoint={deleteTarget?.focal_point}
        previewDetail={deleteTarget ? `${deleteTarget.width} × ${deleteTarget.height}` : undefined}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id)
        }}
        onCancel={() => setDeleteTarget(null)}
      />
      <ToastHost toast={toast.toast} onDismiss={toast.dismiss} />
    </>
  )
}

interface DuplicateTileProps {
  asset: DuplicateAsset
  first: boolean
  selectable: boolean
  selected: boolean
  deleting: boolean
  onToggleSelect: () => void
  onOpen: () => void
  onDelete: () => void
}

function DuplicateTile({ asset, first, selectable, selected, deleting, onToggleSelect, onOpen, onDelete }: DuplicateTileProps) {
  const { t, locale } = useI18n()
  const m = t.duplicates
  return (
    <li className={styles.tile} data-selected={selected}>
      <div className={styles.thumbWrap}>
        <button type="button" className={styles.thumbButton} title={m.open} aria-label={m.open} onClick={onOpen}>
          <img
            className={`${styles.thumb} checkerboard`}
            src={assetUrl(asset.id, 'thumb')}
            style={focalStyle(asset.focal_point)}
            alt=""
            draggable={false}
          />
        </button>
        {first && (
          <span className={styles.firstBadge} title={m.firstBadgeTitle}>
            {m.firstBadge}
          </span>
        )}
        {selectable && (
          <label className={styles.selectBox} title={m.selectForCompare}>
            <input type="checkbox" checked={selected} aria-label={m.selectForCompare} onChange={onToggleSelect} />
          </label>
        )}
      </div>
      {asset.title && (
        <span className={styles.tileTitle} title={asset.title}>
          {asset.title}
        </span>
      )}
      <span className={styles.tileMeta}>
        {asset.width}×{asset.height} · {formatBytes(asset.bytes)}
      </span>
      {/* 狭いタイルでも切れないよう短い日時にし、秒まで含めた値はツールチップに出す。 */}
      <span className={styles.tileMeta} title={formatDateTime(asset.created_at)}>
        {assetKindLabel(asset.kind)} · {formatCompactDateTime(asset.created_at, intlLocale(locale))}
      </span>
      <div className={styles.tileFooter}>
        {/* 狭いタイルでも1行に収まるよう数だけ出し、「類似度」はツールチップと読み上げに回す。 */}
        <span
          className={styles.tileScore}
          title={fmt(t.embeddings.score, { score: formatScore(asset.max_score) })}
          aria-label={fmt(t.embeddings.score, { score: formatScore(asset.max_score) })}
        >
          {formatScore(asset.max_score)}
        </span>
        {/* ADR-0033 12章: 知覚ハッシュが無い画像は CLIP だけで判定したので、印を付ける。 */}
        {asset.hash_missing && (
          <span className={styles.hashMissing} title={m.hashMissingTitle}>
            {m.hashMissing}
          </span>
        )}
        <button type="button" className={styles.deleteButton} disabled={deleting} onClick={onDelete}>
          {m.delete}
        </button>
      </div>
    </li>
  )
}

/**
 * `/stock/group-suggestions` グループの提案(ADR-0056)。ストックの「⋯」からたどる。
 *
 * - 画像の埋め込みで見つけた集まり(`GET /api/asset-groups/suggestions`)を、カードで大きい順に並べる。
 *   自動では何も保存しない。カードの「グループにする」「(グループ名)に追加」を押したものだけを
 *   グループにする(`POST /api/asset-groups/suggestions/accept`)。
 * - カードの画像は押すと提案から外せる(もう一度押すと戻す)。選択の見せ方はストックの選択モードと同じ
 *   (入っている画像に枠と ✓)。表紙は最大 12 枚で、残りは「+n」で開く。
 * - 対象(グループ外だけ / すべて)と粒度(標準 / 細かく)を切り替えられる。説明文は置かない。
 * - 受け入れたカードは畳んで結果だけを出す。ストック・履歴の一覧を取り直す。提案の一覧そのものは
 *   取り直さない(並びが変わらないように)。次に開いたときに取り直す。
 * - 最後に「未分類」(どの提案にも入らなかった、グループ外の画像)を畳んで出す。
 * - サムネイルはレーティングのマスク(ADR-0049)を `AssetThumb` で守る。
 * - タグと作品の訳は、ほかのタグの表示と同じく、言語が日本語で訳を出す設定のときだけ使う。
 * 埋め込みが使えないときは、ストックの入口を出さない。直接開いたときは使えない旨だけを出す。
 */
import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import {
  ApiError,
  acceptGroupSuggestion,
  listGroupSuggestions,
  type AssetSummary,
  type GroupSuggestion,
  type GroupSuggestionGranularity,
  type GroupSuggestionScope,
} from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { AssetThumb } from '../features/content-rating/AssetThumb'
import { thumbRating } from '../features/content-rating/contentRating'
import { embeddingErrorKind, retryEmbeddingQuery } from '../features/embeddings/embeddingErrors'
import { useEmbeddingCapabilitiesState } from '../features/embeddings/useEmbeddingCapabilities'
import {
  GROUP_SUGGESTIONS_QUERY_KEY,
  acceptBody,
  chosenMemberIds,
  formatRate,
  hiddenMemberCount,
  movingCount,
  shownMemberIds,
  toggleExcluded,
} from '../features/group-suggestions/groupSuggestions'
import { invalidateAssetGroupQueries } from '../features/stock/groups/assetGroupQueries'
import { useTagTranslationsEnabled } from '../features/tag-dictionary/useTagTranslations'
import { fmt, useI18n } from '../i18n'
import { focalStyle } from '../lib/focalPoint'
import { useBackNavigate } from '../lib/useBackNavigate'
import styles from './GroupSuggestionsPage.module.css'

export function GroupSuggestionsPage() {
  const { t } = useI18n()
  const m = t.groupSuggestions
  const goBack = useBackNavigate('/')
  const caps = useEmbeddingCapabilitiesState()
  const available = caps.embeddings !== null

  return (
    <div className={styles.page}>
      <button type="button" className={styles.backLink} onClick={goBack}>
        {t.common.back}
      </button>
      <h1 className={styles.title}>{m.title}</h1>
      {caps.isLoading && <p className={styles.placeholder}>{m.loading}</p>}
      {!caps.isLoading && !available && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
      {available && <GroupSuggestionsBody />}
    </div>
  )
}

/** 受け入れた結果(カードを畳んで出す)。 */
interface Accepted {
  kind: 'created' | 'added'
  name: string
  count: number
}

function GroupSuggestionsBody() {
  const { t } = useI18n()
  const m = t.groupSuggestions
  const [scope, setScope] = useState<GroupSuggestionScope>('ungrouped')
  const [granularity, setGranularity] = useState<GroupSuggestionGranularity>('normal')

  const query = useQuery({
    queryKey: [...GROUP_SUGGESTIONS_QUERY_KEY, scope, granularity],
    queryFn: () => listGroupSuggestions({ scope, granularity }),
    retry: retryEmbeddingQuery,
    // 受け入れたカードの状態を持つので、フォーカスで取り直さない(並びが変わらないように)。
    refetchOnWindowFocus: false,
  })
  const data = query.data
  const errorKind = embeddingErrorKind(query.error)
  const assets = useMemo(() => new Map((data?.assets ?? []).map((a) => [a.id, a])), [data])
  const suggestions = data?.suggestions ?? []
  const outlierIds = data?.outlier_ids ?? []

  return (
    <>
      <div className={styles.controls}>
        <Switch
          label={m.scopeLabel}
          value={scope}
          options={[
            ['ungrouped', m.scopeUngrouped],
            ['all', m.scopeAll],
          ]}
          onChange={setScope}
        />
        <Switch
          label={m.granularityLabel}
          value={granularity}
          options={[
            ['normal', m.granularityNormal],
            ['fine', m.granularityFine],
          ]}
          onChange={setGranularity}
        />
      </div>

      {query.isLoading && <p className={styles.placeholder}>{m.loading}</p>}
      {errorKind === 'unavailable' && <p className={styles.placeholder}>{t.embeddings.unavailable}</p>}
      {query.isError && errorKind === null && <p className={styles.error}>{m.loadFailed}</p>}

      {data && (
        <div className={styles.summary}>
          <p className={styles.summaryText}>
            {fmt(m.summary, { count: suggestions.length, scanned: data.scanned })}
          </p>
          {data.truncated && <p className={styles.help}>{fmt(m.truncatedNote, { scanned: data.scanned })}</p>}
        </div>
      )}
      {data && suggestions.length === 0 && <p className={styles.placeholder}>{m.empty}</p>}

      {data && (
        // 範囲・粒度を変えたら、外した画像や入力中の名前を持ち越さない(キーで作り直す)。
        <ol key={`${scope}:${granularity}`} className={styles.cardList}>
          {suggestions.map((suggestion) => (
            <SuggestionCard key={suggestion.id} suggestion={suggestion} assets={assets} />
          ))}
        </ol>
      )}

      {outlierIds.length > 0 && <OutlierBox ids={outlierIds} assets={assets} />}
    </>
  )
}

interface SwitchProps<T extends string> {
  label: string
  value: T
  options: [T, string][]
  onChange: (value: T) => void
}

function Switch<T extends string>({ label, value, options, onChange }: SwitchProps<T>) {
  return (
    <div className={styles.switchRow}>
      <span className={styles.switchLabel} aria-hidden="true">
        {label}
      </span>
      <div className={styles.switch} role="radiogroup" aria-label={label}>
        {options.map(([option, text]) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={value === option}
            data-active={value === option}
            className={styles.switchButton}
            onClick={() => onChange(option)}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  )
}

interface SuggestionCardProps {
  suggestion: GroupSuggestion
  assets: Map<string, AssetSummary>
}

function SuggestionCard({ suggestion, assets }: SuggestionCardProps) {
  const { t } = useI18n()
  const m = t.groupSuggestions
  const queryClient = useQueryClient()
  // 訳(タグ辞書)は、ほかのタグの表示と同じく、言語と設定で出すときだけ使う。
  const translate = useTagTranslationsEnabled()
  const [excluded, setExcluded] = useState<string[]>([])
  const [expanded, setExpanded] = useState(false)
  // 名前の提案(ADR-0056 6章)が来たら初期値にする。今は常に空。
  const [name, setName] = useState(suggestion.name ?? '')
  const [confirming, setConfirming] = useState(false)
  const [accepted, setAccepted] = useState<Accepted | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const target = suggestion.target_group ?? null
  const body = acceptBody(suggestion, name, excluded)
  const chosen = chosenMemberIds(suggestion.member_ids, excluded)
  const moving = movingCount(suggestion, excluded)
  const shown = shownMemberIds(suggestion.member_ids, expanded)
  const hidden = hiddenMemberCount(suggestion.member_ids, expanded)

  const mutation = useMutation({
    mutationFn: acceptGroupSuggestion,
    onSuccess: (row, variables) => {
      setMessage(null)
      setAccepted({ kind: target ? 'added' : 'created', name: row.name, count: variables.asset_ids.length })
      invalidateAssetGroupQueries(queryClient, variables.asset_ids)
      void queryClient.invalidateQueries({ queryKey: ['history-assets'] })
      // 提案の一覧は今は取り直さない(カードの並びが変わらないように)。次に開いたときに取り直す。
      void queryClient.invalidateQueries({ queryKey: GROUP_SUGGESTIONS_QUERY_KEY, refetchType: 'none' })
    },
    onError: (err: unknown) => setMessage(err instanceof ApiError ? err.message : m.acceptFailed),
  })

  function submit() {
    if (!body || mutation.isPending) return
    if (moving > 0 && !confirming) {
      setConfirming(true)
      return
    }
    setConfirming(false)
    mutation.mutate(body)
  }

  if (accepted) {
    return (
      <li className={styles.card} data-accepted="true">
        <p className={styles.acceptedText} role="status">
          {fmt(accepted.kind === 'created' ? m.created : m.added, { name: accepted.name, count: accepted.count })}
        </p>
      </li>
    )
  }

  return (
    <li className={styles.card}>
      <div className={styles.cardHeader}>
        <h2 className={styles.cardHeading}>
          {target ? fmt(m.addTo, { name: target.name }) : fmt(m.count, { count: chosen.length })}
          {target && <span className={styles.cardMeta}>{fmt(m.count, { count: chosen.length })}</span>}
        </h2>
        {suggestion.franchise && (
          <span className={styles.franchise}>
            {fmt(m.franchise, {
              name: (translate && suggestion.franchise.label) || suggestion.franchise.name,
              rate: formatRate(suggestion.franchise.rate),
            })}
          </span>
        )}
      </div>

      {(suggestion.top_tags ?? []).length > 0 && (
        <ul className={styles.tags}>
          {(suggestion.top_tags ?? []).map((tag) => (
            <li key={tag.tag} className={styles.tag} title={`${tag.tag} · ${formatRate(tag.rate)}`}>
              {translate ? tag.label : tag.tag}
            </li>
          ))}
        </ul>
      )}

      <ul className={styles.tiles}>
        {shown.map((assetId) => (
          <MemberTile
            key={assetId}
            assetId={assetId}
            asset={assets.get(assetId)}
            included={!excluded.includes(assetId)}
            onToggle={() => setExcluded((prev) => toggleExcluded(prev, assetId))}
          />
        ))}
        {hidden > 0 && (
          <li className={styles.tileItem}>
            <button type="button" className={styles.moreTile} onClick={() => setExpanded(true)}>
              {fmt(m.moreMembers, { count: hidden })}
            </button>
          </li>
        )}
      </ul>
      {expanded && suggestion.member_ids.length > shownMemberIds(suggestion.member_ids, false).length && (
        <button type="button" className={styles.linkButton} onClick={() => setExpanded(false)}>
          {m.showLess}
        </button>
      )}

      <form
        className={styles.cardFooter}
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        {!target && (
          <input
            type="text"
            className={styles.nameInput}
            value={name}
            maxLength={100}
            aria-label={m.nameLabel}
            onChange={(e) => setName(e.target.value)}
          />
        )}
        <button type="submit" className={styles.primaryButton} disabled={!body || mutation.isPending}>
          {target ? fmt(m.addTo, { name: target.name }) : m.create}
        </button>
      </form>
      {message && <p className={styles.error}>{message}</p>}

      <ConfirmDialog
        open={confirming}
        message={fmt(m.moveConfirm, { count: moving })}
        confirmLabel={m.moveConfirmLabel}
        onConfirm={submit}
        onCancel={() => setConfirming(false)}
      />
    </li>
  )
}

interface MemberTileProps {
  assetId: string
  asset: AssetSummary | undefined
  included: boolean
  onToggle: () => void
}

function MemberTile({ assetId, asset, included, onToggle }: MemberTileProps) {
  const { t } = useI18n()
  const m = t.groupSuggestions
  const label = included ? m.exclude : m.include
  return (
    <li className={styles.tileItem} data-included={included}>
      <button
        type="button"
        className={styles.tileButton}
        aria-pressed={included}
        aria-label={label}
        title={label}
        onClick={onToggle}
      >
        <AssetThumb
          assetId={assetId}
          rating={asset ? thumbRating(asset) : undefined}
          withheld={asset?.content_withheld}
          className={`${styles.thumb} checkerboard`}
          style={focalStyle(asset?.focal_point)}
          alt=""
          draggable={false}
        />
        {included && (
          <span className={styles.checkBadge} aria-hidden="true">
            ✓
          </span>
        )}
      </button>
    </li>
  )
}

function OutlierBox({ ids, assets }: { ids: string[]; assets: Map<string, AssetSummary> }) {
  const { t } = useI18n()
  const m = t.groupSuggestions
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  return (
    <details className={styles.outliers} open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className={styles.outliersSummary}>
        {m.outliers}
        <span className={styles.cardMeta}>{fmt(m.count, { count: ids.length })}</span>
      </summary>
      {/* 畳んでいる間はサムネイルを読みに行かない。 */}
      {open && (
        <ul className={styles.tiles}>
          {ids.map((assetId) => {
            const asset = assets.get(assetId)
            return (
              <li key={assetId} className={styles.tileItem}>
                <button
                  type="button"
                  className={styles.tileButton}
                  aria-label={m.open}
                  title={m.open}
                  onClick={() => navigate(`/assets/${assetId}`)}
                >
                  <AssetThumb
                    assetId={assetId}
                    rating={asset ? thumbRating(asset) : undefined}
                    withheld={asset?.content_withheld}
                    className={`${styles.thumb} checkerboard`}
                    style={focalStyle(asset?.focal_point)}
                    alt=""
                    draggable={false}
                  />
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </details>
  )
}

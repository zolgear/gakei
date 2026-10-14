/**
 * ビューアの「レーティング」の節(ADR-0049 4章)。タイトルの節の下に置く(置き場所は Viewer が決める)。
 * - 段階のバッジ(グリーンも出す)と、自動か人か。
 * - 段階を選ぶ操作(5つのボタン)と、人が選んでいるときの「自動に戻す」。
 * - 「詳しく見る」で、rating の4つの確率、引き上げのタグ(段階を上げたものに印)、モデル、規則の版、
 *   判定の時刻、人が上書きしているときの自動の段階を出す。
 * - 再判定のボタンと、待ち・判定中・失敗の状態。判定中は Viewer のクエリが取り直す
 *   (`contentRatingPollInterval`)。
 *
 * 節を出すかは `shouldShowContentRatingSection`(管理者設定で無効、かつ段階が無ければ出さない)。
 * 更新の応答は Asset 詳細のキャッシュ(`['asset', id]`)に書き戻し、一覧(履歴、ストック、検索など)を
 * 取り直させて、カードのバッジとマスクを揃える。
 */
import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  getAnnotationSettings,
  judgeAssetContentRating,
  updateAssetContentRating,
  type AssetContentRatingResponse,
  type AssetDetail,
  type ContentRatingDetail,
  type ContentRatingLevel,
} from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { formatDateTime } from '../../lib/format'
import { ANNOTATION_SETTINGS_QUERY_KEY } from '../settings/queryKeys'
import {
  CONTENT_RATING_LEVELS,
  RATING_SCORE_KEYS,
  canEditContentRating,
  canRequestContentRating,
  formatProbability,
  isContentRatingPending,
  mergeContentRating,
  shouldShowContentRatingSection,
} from './contentRating'
import { RatingBadge } from './RatingBadge'
import { invalidateContentRatingLists, useRefreshListsWhenJudged } from './contentRatingRefresh'
import sectionStyles from '../viewer/AssetAnnotationSection.module.css'
import styles from './ContentRatingSection.module.css'

interface ContentRatingSectionProps {
  asset: AssetDetail
}

export function ContentRatingSection({ asset }: ContentRatingSectionProps) {
  const { t } = useI18n()
  const c = t.contentRating.section
  const queryClient = useQueryClient()
  const detailsId = useId()
  const detail = asset.content_rating_detail ?? null
  const level = detail?.level ?? null
  const pending = isContentRatingPending(detail)
  const editable = canEditContentRating(asset)

  const settingsQuery = useQuery({
    queryKey: ANNOTATION_SETTINGS_QUERY_KEY,
    queryFn: getAnnotationSettings,
    staleTime: 60_000,
  })
  const showJudge = canRequestContentRating(asset, settingsQuery.data?.content_rating_usable)

  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  // 別の Asset に切り替わったら、エラーと展開を戻す(レンダー中に前回値と比べてリセットする)。
  const [stateAssetId, setStateAssetId] = useState(asset.id)
  if (stateAssetId !== asset.id) {
    setStateAssetId(asset.id)
    setError(null)
    setExpanded(false)
  }

  function applyResponse(response: AssetContentRatingResponse) {
    queryClient.setQueryData<AssetDetail>(['asset', response.asset_id], (prev) =>
      prev ? mergeContentRating(prev, response) : prev,
    )
    invalidateContentRatingLists(queryClient)
    setError(null)
  }

  function handleError(err: unknown, fallback: string) {
    setError(err instanceof ApiError ? err.message : fallback)
  }

  // 判定が終わったら一覧を取り直す(詳細は Viewer のポーリングで最新になっている)。
  useRefreshListsWhenJudged(detail?.status)

  const levelMutation = useMutation({
    mutationFn: (next: ContentRatingLevel | null) => updateAssetContentRating(asset.id, next),
    onSuccess: applyResponse,
    onError: (err) => handleError(err, c.saveFailed),
  })

  const judgeMutation = useMutation({
    mutationFn: () => judgeAssetContentRating(asset.id),
    onSuccess: applyResponse,
    onError: (err) => handleError(err, c.judgeFailed),
  })

  if (!shouldShowContentRatingSection(asset, settingsQuery.data?.content_rating_enabled)) return null

  const busy = levelMutation.isPending
  const hasDetails = Boolean(detail && (detail.scores || detail.model || detail.judged_at))

  return (
    <div className={sectionStyles.section}>
      <h3 className={sectionStyles.heading}>{c.heading}</h3>

      <div className={styles.levelRow}>
        {level ? <RatingBadge level={level} /> : <p className={sectionStyles.empty}>{c.noLevel}</p>}
        {level && detail?.source && (
          <span className={styles.source} data-source={detail.source}>
            {detail.source === 'user' ? c.sourceUser : c.sourceAuto}
          </span>
        )}
      </div>

      {editable && (
        <div className={styles.controls}>
          <div className={styles.levelButtons} role="group" aria-label={c.levelSelectLabel}>
            {CONTENT_RATING_LEVELS.map((l) => (
              <button
                key={l}
                type="button"
                className={styles.levelButton}
                data-level={l}
                aria-pressed={level === l && detail?.source === 'user'}
                data-current={level === l ? 'true' : undefined}
                disabled={busy}
                onClick={() => levelMutation.mutate(l)}
              >
                {t.contentRating.levels[l]}
              </button>
            ))}
          </div>
          {detail?.source === 'user' && (
            <button
              type="button"
              className={sectionStyles.button}
              disabled={busy}
              onClick={() => levelMutation.mutate(null)}
            >
              {c.revertToAuto}
            </button>
          )}
        </div>
      )}

      {(detail?.status || showJudge) && (
        <div className={sectionStyles.statusRow}>
          {detail?.status && <JudgeStatusLine detail={detail} />}
          {showJudge && (
            <button
              type="button"
              className={sectionStyles.button}
              title={c.rejudgeTooltip}
              disabled={pending || judgeMutation.isPending}
              onClick={() => judgeMutation.mutate()}
            >
              {detail?.status ? c.rejudge : c.judge}
            </button>
          )}
        </div>
      )}

      {hasDetails && detail && (
        <>
          <button
            type="button"
            className={sectionStyles.toggleButton}
            aria-expanded={expanded}
            aria-controls={detailsId}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? c.hideDetails : c.showDetails}
          </button>
          {expanded && <ContentRatingDetails id={detailsId} detail={detail} />}
        </>
      )}

      {error && (
        <p className={sectionStyles.error} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

function JudgeStatusLine({ detail }: { detail: ContentRatingDetail }) {
  const { t } = useI18n()
  const c = t.contentRating.section
  const status = detail.status
  if (status === 'queued' || status === 'running') {
    return (
      <p className={sectionStyles.status} data-status={status} role="status">
        <span className={sectionStyles.spinner} aria-hidden="true" />
        {status === 'queued' ? c.statusQueued : c.statusRunning}
      </p>
    )
  }
  if (status === 'failed') {
    return (
      <p className={sectionStyles.status} data-status="failed">
        {detail.error ? fmt(c.statusFailed, { error: detail.error }) : c.statusFailedNoReason}
      </p>
    )
  }
  // 判定済みの時刻は詳細に出すので、ここでは何も出さない(ボタンだけを右に寄せる)。
  return <span />
}

function ContentRatingDetails({ id, detail }: { id: string; detail: ContentRatingDetail }) {
  const { t } = useI18n()
  const c = t.contentRating.section
  const scores = detail.scores ?? null
  const signals = detail.signals ?? []
  const overridden = detail.source === 'user'
  return (
    <div id={id} className={styles.details}>
      {scores && (
        <div className={styles.detailBlock}>
          <p className={styles.detailLabel}>{c.scoresLabel}</p>
          <ul className={styles.scoreList}>
            {RATING_SCORE_KEYS.map((key) => {
              const value = scores[key]
              return (
                <li key={key} className={styles.scoreRow}>
                  <span className={styles.scoreName}>{key}</span>
                  <span className={styles.scoreBar} aria-hidden="true">
                    <span style={{ width: `${Math.round(Math.min(1, Math.max(0, value ?? 0)) * 100)}%` }} />
                  </span>
                  <span className={styles.scoreValue}>{formatProbability(value)}</span>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      <div className={styles.detailBlock}>
        <p className={styles.detailLabel}>{c.signalsLabel}</p>
        {signals.length > 0 ? (
          <ul className={styles.signalList}>
            {signals.map((signal) => (
              <li key={signal.tag} className={styles.signalRow} data-raised={signal.raised ? 'true' : undefined}>
                <span className={styles.signalTag}>{signal.tag}</span>
                <span className={styles.scoreValue}>{formatProbability(signal.score)}</span>
                <RatingBadge level={signal.level} size="corner" />
                {signal.raised && <span className={styles.raised}>{c.raised}</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className={sectionStyles.empty}>{c.signalsNone}</p>
        )}
      </div>

      <dl className={styles.metaList}>
        {overridden && (
          <>
            <dt>{c.autoLevelLabel}</dt>
            <dd>{detail.auto_level ? <RatingBadge level={detail.auto_level} /> : '—'}</dd>
          </>
        )}
        {detail.model && (
          <>
            <dt>{c.modelLabel}</dt>
            <dd className={styles.mono}>{detail.model}</dd>
          </>
        )}
        {detail.rules_version != null && (
          <>
            <dt>{c.rulesVersionLabel}</dt>
            <dd className={styles.mono}>{detail.rules_version}</dd>
          </>
        )}
        {detail.judged_at && (
          <>
            <dt>{c.judgedAtLabel}</dt>
            <dd>{formatDateTime(detail.judged_at)}</dd>
          </>
        )}
      </dl>
    </div>
  )
}

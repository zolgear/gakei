/**
 * ビューアの「似た画像」節(ADR-0033 8章)。使うモデルで、この画像に近い画像を上位 12 件まで
 * サムネイルで並べ、続きは検索ページ(`/search?similar=<id>`)で見る。
 *
 * - 埋め込みが使えないとき(capabilities)、マスク、削除済みの画像では出さない(呼び出し側と
 *   ここで判断する)。サーバーが `embeddings_unavailable` / `embedding_not_supported` を返したときも隠す。
 * - この画像のベクトルが計算中(`embedding_pending`)なら、終わるまで取り直す。
 * - まだ無い(`embedding_missing`)・失敗した(`embedding_failed`)なら、「計算する」を出す
 *   (`POST /api/assets/{id}/embedding`)。
 * - 節は畳める。開閉は閲覧者のブラウザに覚える。畳んでいる間は取得しない。
 */
import { useId, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { similarAssets } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import { EmbeddingStateNotice } from '../embeddings/EmbeddingStateNotice'
import { embeddingErrorKind, embeddingPollInterval, retryEmbeddingQuery } from '../embeddings/embeddingErrors'
import { formatScore } from '../embeddings/duplicates'
import { VIEWER_SIMILAR_LIMIT, similarAssetsQueryKey } from '../embeddings/similarAssets'
import { buildSimilarSearchPath } from '../search/searchQuerySync'
import { loadSimilarOpen, saveSimilarOpen } from './similarOpenStorage'
import styles from './SimilarAssetsSection.module.css'

export function SimilarAssetsSection({ assetId }: { assetId: string }) {
  const { t } = useI18n()
  const m = t.embeddings
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(loadSimilarOpen)
  const bodyId = useId()

  const queryKey = similarAssetsQueryKey(assetId, VIEWER_SIMILAR_LIMIT)
  const query = useQuery({
    queryKey,
    queryFn: () => similarAssets(assetId, VIEWER_SIMILAR_LIMIT),
    enabled: open,
    retry: retryEmbeddingQuery,
    refetchInterval: (q) => embeddingPollInterval(q.state.error),
  })

  const errorKind = embeddingErrorKind(query.error)
  // 使えない・対象外は節ごと隠す(無効な機能は隠す)。
  if (errorKind === 'unavailable' || errorKind === 'notSupported') return null

  function toggle() {
    setOpen((prev) => {
      saveSimilarOpen(!prev)
      return !prev
    })
  }

  const assets = query.data?.assets ?? []

  return (
    <div className={styles.section}>
      <h3 className={styles.heading}>
        <button
          type="button"
          className={styles.header}
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={toggle}
        >
          <span className={styles.chevron} data-open={open} aria-hidden="true">
            ›
          </span>
          {t.viewer.similar.heading}
        </button>
      </h3>

      {open && (
        <div id={bodyId} className={styles.body}>
          {query.isLoading && <p className={styles.note}>{t.viewer.loading}</p>}

          <EmbeddingStateNotice
            assetId={assetId}
            kind={errorKind}
            onRequested={() => void queryClient.invalidateQueries({ queryKey })}
          />
          {query.isError && errorKind === null && <p className={styles.error}>{m.loadFailed}</p>}

          {query.isSuccess && assets.length === 0 && <p className={styles.note}>{m.noSimilar}</p>}
          {assets.length > 0 && (
            <>
              <ul className={styles.grid}>
                {assets.map((hit) => {
                  const score = fmt(m.score, { score: formatScore(hit.score) })
                  const label = hit.title ? `${hit.title} · ${score}` : score
                  return (
                    <li key={hit.id}>
                      <Link to={`/assets/${hit.id}`} className={styles.tile} title={label} aria-label={label}>
                        <img
                          src={assetUrl(hit.id, 'thumb')}
                          alt=""
                          draggable={false}
                          className={`${styles.thumb} checkerboard`}
                        />
                      </Link>
                    </li>
                  )
                })}
              </ul>
              <Link to={buildSimilarSearchPath(assetId)} className={styles.more}>
                {t.viewer.similar.showMore}
              </Link>
            </>
          )}
        </div>
      )}
    </div>
  )
}

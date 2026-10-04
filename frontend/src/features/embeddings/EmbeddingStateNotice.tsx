/**
 * 起点の画像のベクトルが無いために似た画像を出せないときの案内(ADR-0033 8章)。ビューアの
 * 「似た画像」と、検索ページの `?similar=` で使う。
 * - 計算中(`embedding_pending`): その旨だけ出す(取り直しは呼び出し側のクエリが行う)。
 * - まだ無い(`embedding_missing`)・失敗(`embedding_failed`): 「計算する」で
 *   `POST /api/assets/{id}/embedding` を送り、`onRequested` で呼び出し側に取り直させる。
 * それ以外の種類では何も出さない。
 */
import { useMutation } from '@tanstack/react-query'
import { ApiError, requestAssetEmbedding } from '../../api/client'
import { useI18n } from '../../i18n'
import type { EmbeddingErrorKind } from './embeddingErrors'
import styles from './EmbeddingStateNotice.module.css'

interface EmbeddingStateNoticeProps {
  assetId: string
  kind: EmbeddingErrorKind | null
  onRequested: () => void
}

export function EmbeddingStateNotice({ assetId, kind, onRequested }: EmbeddingStateNoticeProps) {
  const { t } = useI18n()
  const m = t.embeddings
  const mutation = useMutation({
    mutationFn: () => requestAssetEmbedding(assetId),
    onSuccess: onRequested,
  })
  const error = mutation.error ? (mutation.error instanceof ApiError ? mutation.error.message : m.requestFailed) : null

  if (kind === 'pending') {
    return (
      <p className={styles.note} role="status">
        {m.pending}
      </p>
    )
  }
  if (kind !== 'missing' && kind !== 'failed') return null
  return (
    <div className={styles.wrap}>
      <div className={styles.row}>
        <p className={styles.note}>{kind === 'missing' ? m.missing : m.failed}</p>
        <button
          type="button"
          className={styles.button}
          disabled={mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {kind === 'missing' ? m.compute : m.recompute}
        </button>
      </div>
      {error && <p className={styles.error}>{error}</p>}
    </div>
  )
}

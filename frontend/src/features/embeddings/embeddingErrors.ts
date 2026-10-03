/**
 * 埋め込みの API(ADR-0033 7章)の 409 を、画面の出し分けに使う種類に直す純粋関数。
 * サーバーは `detail.code` で理由を返す(`ApiError.code`)。
 *
 * - `unavailable`: 埋め込みが無効、または使えるモデルが無い。画面からは入口ごと隠す。
 * - `pending`: その画像のベクトルを計算中。終わるまで取り直す。
 * - `missing`: まだ計算していない。「計算する」を出す。
 * - `failed`: 計算に失敗した。「計算し直す」を出す。
 * - `notSupported`: マスクは対象外。何も出さない。
 */
import { ApiError } from '../../api/client'

export type EmbeddingErrorKind = 'unavailable' | 'pending' | 'missing' | 'failed' | 'notSupported'

const CODE_TO_KIND: Record<string, EmbeddingErrorKind> = {
  embeddings_unavailable: 'unavailable',
  embedding_pending: 'pending',
  embedding_missing: 'missing',
  embedding_failed: 'failed',
  embedding_not_supported: 'notSupported',
}

/** 409 の `code` から種類を引く。埋め込みの 409 でなければ null(通信の失敗などとして扱う)。 */
export function embeddingErrorKind(err: unknown): EmbeddingErrorKind | null {
  if (!(err instanceof ApiError) || err.status !== 409 || !err.code) return null
  return CODE_TO_KIND[err.code] ?? null
}

/**
 * 埋め込みの検索系のクエリの再試行の判定(TanStack Query の `retry`)。409(状態による拒否)と
 * 4xx は何度送っても同じなので再試行しない。それ以外(通信の失敗、5xx)は1回だけ再試行する。
 */
export function retryEmbeddingQuery(failureCount: number, err: unknown): boolean {
  if (err instanceof ApiError && err.status >= 400 && err.status < 500) return false
  return failureCount < 1
}

/** 計算中の間だけ取り直す間隔(ミリ秒)。計算中でなければ false。 */
export const EMBEDDING_PENDING_POLL_MS = 3000

export function embeddingPollInterval(err: unknown): number | false {
  return embeddingErrorKind(err) === 'pending' ? EMBEDDING_PENDING_POLL_MS : false
}

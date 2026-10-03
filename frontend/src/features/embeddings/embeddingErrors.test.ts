import { describe, expect, it } from 'vitest'
import { ApiError } from '../../api/client'
import {
  EMBEDDING_PENDING_POLL_MS,
  embeddingErrorKind,
  embeddingPollInterval,
  retryEmbeddingQuery,
} from './embeddingErrors'

const conflict = (code: string | null) => new ApiError(409, 'message', code)

describe('embeddingErrorKind', () => {
  it('409 の code を画面の出し分けの種類に直す', () => {
    expect(embeddingErrorKind(conflict('embeddings_unavailable'))).toBe('unavailable')
    expect(embeddingErrorKind(conflict('embedding_pending'))).toBe('pending')
    expect(embeddingErrorKind(conflict('embedding_missing'))).toBe('missing')
    expect(embeddingErrorKind(conflict('embedding_failed'))).toBe('failed')
    expect(embeddingErrorKind(conflict('embedding_not_supported'))).toBe('notSupported')
  })

  it('知らない code、code の無い 409、409 以外、ApiError 以外は null', () => {
    expect(embeddingErrorKind(conflict('something_else'))).toBeNull()
    expect(embeddingErrorKind(conflict(null))).toBeNull()
    expect(embeddingErrorKind(new ApiError(404, 'not found', 'embedding_pending'))).toBeNull()
    expect(embeddingErrorKind(new Error('network'))).toBeNull()
    expect(embeddingErrorKind(null)).toBeNull()
  })
})

describe('retryEmbeddingQuery', () => {
  it('4xx(409 を含む)は再試行しない', () => {
    expect(retryEmbeddingQuery(0, conflict('embedding_pending'))).toBe(false)
    expect(retryEmbeddingQuery(0, new ApiError(404, 'x'))).toBe(false)
    expect(retryEmbeddingQuery(0, new ApiError(422, 'x'))).toBe(false)
  })

  it('通信の失敗と 5xx は1回だけ再試行する', () => {
    expect(retryEmbeddingQuery(0, new Error('network'))).toBe(true)
    expect(retryEmbeddingQuery(0, new ApiError(503, 'x'))).toBe(true)
    expect(retryEmbeddingQuery(1, new ApiError(503, 'x'))).toBe(false)
  })
})

describe('embeddingPollInterval', () => {
  it('計算中の間だけ取り直す', () => {
    expect(embeddingPollInterval(conflict('embedding_pending'))).toBe(EMBEDDING_PENDING_POLL_MS)
    expect(embeddingPollInterval(conflict('embedding_missing'))).toBe(false)
    expect(embeddingPollInterval(null)).toBe(false)
  })
})

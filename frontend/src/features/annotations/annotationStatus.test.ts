import { describe, expect, it } from 'vitest'
import type { AssetDetail } from '../../api/client'
import {
  ANNOTATION_POLL_INTERVAL_MS,
  annotationJustFinished,
  annotationPollInterval,
  canEditAnnotation,
  canRequestAnnotation,
  isAnnotationPending,
  mergeAnnotation,
  supportsAnnotation,
} from './annotationStatus'

describe('isAnnotationPending / annotationPollInterval', () => {
  it('queued と running だけポーリングする', () => {
    expect(isAnnotationPending({ status: 'queued' })).toBe(true)
    expect(isAnnotationPending({ status: 'running' })).toBe(true)
    expect(isAnnotationPending({ status: 'succeeded' })).toBe(false)
    expect(isAnnotationPending({ status: 'failed' })).toBe(false)
    expect(isAnnotationPending(null)).toBe(false)
    expect(annotationPollInterval({ annotation: { status: 'running' } })).toBe(ANNOTATION_POLL_INTERVAL_MS)
    expect(annotationPollInterval({ annotation: null })).toBe(false)
    expect(annotationPollInterval(undefined)).toBe(false)
  })
})

describe('annotationJustFinished', () => {
  it('推定中から終わった状態へ移ったときだけ true', () => {
    expect(annotationJustFinished('running', 'succeeded')).toBe(true)
    expect(annotationJustFinished('queued', 'failed')).toBe(true)
    expect(annotationJustFinished('queued', 'running')).toBe(false)
    expect(annotationJustFinished(null, 'succeeded')).toBe(false)
    expect(annotationJustFinished('succeeded', 'succeeded')).toBe(false)
  })
})

describe('supportsAnnotation / canEditAnnotation / canRequestAnnotation', () => {
  it('マスクは対象外', () => {
    expect(supportsAnnotation({ kind: 'mask' })).toBe(false)
    expect(supportsAnnotation({ kind: 'upload' })).toBe(true)
  })

  it('削除済みは編集できない', () => {
    expect(canEditAnnotation({ kind: 'generated', deleted_at: '2026-09-28T00:00:00Z' })).toBe(false)
    expect(canEditAnnotation({ kind: 'generated', deleted_at: null })).toBe(true)
  })

  it('使えるエンジンが無ければ再推定を出さない', () => {
    const asset = { kind: 'sketch' as const, deleted_at: null }
    expect(canRequestAnnotation(asset, [])).toBe(false)
    expect(canRequestAnnotation(asset, undefined)).toBe(false)
    expect(canRequestAnnotation(asset, ['onnx'])).toBe(true)
    expect(canRequestAnnotation({ kind: 'mask', deleted_at: null }, ['vlm'])).toBe(false)
  })
})

describe('mergeAnnotation', () => {
  it('タイトル・タグ・状態だけを置き換える', () => {
    const asset = { id: 'a1', kind: 'upload', title: null, tags: [], width: 10 } as unknown as AssetDetail
    const merged = mergeAnnotation(asset, {
      asset_id: 'a1',
      title: '夕暮れ',
      title_source: 'user',
      tags: [{ name: 'sky', source: 'auto' }],
      annotation: null,
    })
    expect(merged.title).toBe('夕暮れ')
    expect(merged.title_source).toBe('user')
    expect(merged.tags).toEqual([{ name: 'sky', source: 'auto' }])
    expect(merged.width).toBe(10)
  })
})

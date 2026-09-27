import { describe, expect, it } from 'vitest'
import { resolveStudioRestoreRunId, type StudioReturnSnapshot } from './studioRestore'

const RUN_ID = '11111111-1111-1111-1111-111111111111'

function baseInput(overrides: {
  hasAssetParam?: boolean
  hasRunParam?: boolean
  isNewRunSignal?: boolean
  lastReturn?: Partial<StudioReturnSnapshot>
} = {}) {
  return {
    hasAssetParam: false,
    hasRunParam: false,
    isNewRunSignal: false,
    ...overrides,
    lastReturn: {
      runId: RUN_ID,
      status: 'running' as const,
      ...overrides.lastReturn,
    },
  }
}

describe('resolveStudioRestoreRunId', () => {
  it('running を追っていて、URL にパラメーターが無ければ復元する', () => {
    expect(resolveStudioRestoreRunId(baseInput())).toBe(RUN_ID)
  })

  it('queued を追っていても復元する', () => {
    expect(resolveStudioRestoreRunId(baseInput({ lastReturn: { status: 'queued' } }))).toBe(RUN_ID)
  })

  it('?asset= が付いていれば復元しない', () => {
    expect(resolveStudioRestoreRunId(baseInput({ hasAssetParam: true }))).toBeNull()
  })

  it('?run= が既に付いていれば復元しない(URL が正)', () => {
    expect(resolveStudioRestoreRunId(baseInput({ hasRunParam: true }))).toBeNull()
  })

  it('「新規生成」の合図の直後は復元しない', () => {
    expect(resolveStudioRestoreRunId(baseInput({ isNewRunSignal: true }))).toBeNull()
  })

  it('追っていた Run が無ければ null', () => {
    expect(resolveStudioRestoreRunId(baseInput({ lastReturn: { runId: null } }))).toBeNull()
  })

  it.each(['succeeded', 'failed', 'canceled'] as const)(
    '終了済み(%s)なら復元しない',
    (status) => {
      expect(resolveStudioRestoreRunId(baseInput({ lastReturn: { status } }))).toBeNull()
    },
  )

  it('status がまだ分からない(null)なら復元しない', () => {
    expect(resolveStudioRestoreRunId(baseInput({ lastReturn: { status: null } }))).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'
import { effectiveRunStatus, isTerminalRunStatus, shouldWatchRunEvents } from './runCardState'

describe('runCardState', () => {
  it('詳細が終わっていればそれを使い、そうでなければ SSE の値を優先する', () => {
    expect(effectiveRunStatus('succeeded', 'running')).toBe('succeeded')
    expect(effectiveRunStatus('running', 'succeeded')).toBe('succeeded')
    expect(effectiveRunStatus('queued', null)).toBe('queued')
    expect(effectiveRunStatus(undefined, undefined)).toBe('queued')
  })

  it('終わりの状態と、SSE をつなぐ状態', () => {
    expect(isTerminalRunStatus('canceled')).toBe(true)
    expect(isTerminalRunStatus('running')).toBe(false)
    expect(isTerminalRunStatus(null)).toBe(false)
    expect(shouldWatchRunEvents('running')).toBe(true)
    expect(shouldWatchRunEvents('queued')).toBe(false)
  })
})

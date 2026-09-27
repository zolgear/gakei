import { describe, expect, it } from 'vitest'
import { applyRunEvent, initialRunEventsAccumulator } from './runEventsReducer'
import type { RunEvent } from '../../api/client'

function statusEvent(status: RunEvent['status']): RunEvent {
  return { type: 'status', status }
}

function progressEvent(value: number, max: number, node: string | null = null): RunEvent {
  return { type: 'progress', value, max, node }
}

function partialEvent(index: number): RunEvent {
  return { type: 'partial', index }
}

describe('applyRunEvent', () => {
  it('status イベントは latestStatus を置き換える', () => {
    const state = applyRunEvent(initialRunEventsAccumulator(), statusEvent('running'))
    expect(state.latestStatus?.status).toBe('running')
    const next = applyRunEvent(state, statusEvent('succeeded'))
    expect(next.latestStatus?.status).toBe('succeeded')
  })

  it('progress イベントは最新の1件だけを保持する(積み上げない)', () => {
    const state = applyRunEvent(initialRunEventsAccumulator(), progressEvent(2, 10))
    expect(state.progress).toEqual({ type: 'progress', value: 2, max: 10, node: null })
    const next = applyRunEvent(state, progressEvent(5, 10))
    expect(next.progress).toEqual({ type: 'progress', value: 5, max: 10, node: null })
  })

  it('partial イベントは配列に積む', () => {
    let state = initialRunEventsAccumulator()
    state = applyRunEvent(state, partialEvent(0))
    state = applyRunEvent(state, partialEvent(1))
    expect(state.partials.map((e) => e.index)).toEqual([0, 1])
  })

  it('progress は partials・latestStatus に影響しない', () => {
    let state = initialRunEventsAccumulator()
    state = applyRunEvent(state, statusEvent('running'))
    state = applyRunEvent(state, partialEvent(0))
    state = applyRunEvent(state, progressEvent(1, 4))
    expect(state.latestStatus?.status).toBe('running')
    expect(state.partials).toHaveLength(1)
    expect(state.progress?.value).toBe(1)
  })

  it('進捗が一度も届かなければ progress は null のまま', () => {
    const state = applyRunEvent(initialRunEventsAccumulator(), statusEvent('queued'))
    expect(state.progress).toBeNull()
  })
})

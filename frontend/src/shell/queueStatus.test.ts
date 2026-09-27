import { describe, expect, it } from 'vitest'
import { countQueue, hasActiveRuns } from './queueStatus'
import type { RunSummary } from '../api/client'

function run(status: RunSummary['status']): RunSummary {
  return {
    id: crypto.randomUUID(),
    provider: 'openai',
    operation: 'generate',
    model: 'gpt-image-2.5-sunburst',
    status,
    prompt: 'p',
    queued_at: '2026-01-01T00:00:00Z',
    input_count: 0,
    descendant_run_count: 0,
  }
}

describe('countQueue', () => {
  it('running と queued だけを数える', () => {
    const runs = [run('running'), run('queued'), run('queued'), run('succeeded'), run('failed')]
    expect(countQueue(runs)).toEqual({ running: 1, queued: 2 })
  })

  it('空配列は 0/0', () => {
    expect(countQueue([])).toEqual({ running: 0, queued: 0 })
  })
})

describe('hasActiveRuns', () => {
  it('running か queued があれば true', () => {
    expect(hasActiveRuns({ running: 1, queued: 0 })).toBe(true)
    expect(hasActiveRuns({ running: 0, queued: 1 })).toBe(true)
  })

  it('どちらも0なら false', () => {
    expect(hasActiveRuns({ running: 0, queued: 0 })).toBe(false)
  })
})

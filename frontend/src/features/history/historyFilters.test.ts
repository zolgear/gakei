import { describe, expect, it } from 'vitest'
import { filterRuns } from './historyFilters'
import type { RunSummary } from '../../api/client'

function run(overrides: Partial<RunSummary>): RunSummary {
  return {
    id: crypto.randomUUID(),
    provider: 'openai',
    operation: 'generate',
    model: 'gpt-image-2.5-sunburst',
    status: 'succeeded',
    prompt: 'p',
    queued_at: '2026-01-01T00:00:00Z',
    input_count: 0,
    descendant_run_count: 0,
    ...overrides,
  }
}

describe('filterRuns', () => {
  const runs = [
    run({ status: 'succeeded' }),
    run({ status: 'failed' }),
    run({ status: 'succeeded', input_count: 1 }),
    run({ status: 'running' }),
  ]

  it('status=all なら全件', () => {
    expect(filterRuns(runs, 'all')).toHaveLength(4)
  })

  it('status=succeeded で絞り込む', () => {
    const result = filterRuns(runs, 'succeeded')
    expect(result).toHaveLength(2)
    expect(result.every((r) => r.status === 'succeeded')).toBe(true)
  })

  it('status=failed で絞り込む', () => {
    const result = filterRuns(runs, 'failed')
    expect(result).toHaveLength(1)
  })
})

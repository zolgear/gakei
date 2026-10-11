import { describe, expect, it } from 'vitest'
import type { RunSummary, SemanticRunHit } from '../../api/client'
import { promptRunCards } from './promptSearchResults'

function summary(id: string): RunSummary {
  return {
    id,
    provider: 'openai',
    operation: 'generate',
    model: 'gpt-image-2',
    status: 'succeeded',
    prompt: `prompt ${id}`,
    params: {},
    queued_at: '2026-10-11T00:00:00Z',
    outputs: [],
    input_count: 0,
  } as unknown as RunSummary
}

function hit(id: string, score: number): SemanticRunHit {
  return {
    id,
    operation: 'generate',
    model: 'gpt-image-2',
    created_at: '2026-10-11T00:00:00Z',
    prompt: `prompt ${id}`,
    prompt_truncated: false,
    score,
    run: summary(id),
  }
}

describe('promptRunCards', () => {
  it('応答の順のまま、Run の要約と類似度(小数3桁)を並べる', () => {
    const cards = promptRunCards({ runs: [hit('a', 0.91234), hit('b', 0.5)] })
    expect(cards.map((c) => c.run.id)).toEqual(['a', 'b'])
    expect(cards.map((c) => c.score)).toEqual(['0.912', '0.500'])
  })

  it('応答が無い・空なら空', () => {
    expect(promptRunCards(undefined)).toEqual([])
    expect(promptRunCards({ runs: [] })).toEqual([])
  })
})

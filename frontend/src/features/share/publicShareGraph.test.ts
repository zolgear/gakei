import { describe, expect, it } from 'vitest'
import type { PublicShareResponse } from '../../api/client'
import { toLineageResponse } from './publicShareGraph'

const base = {
  mime: 'image/png',
  width: 10,
  height: 10,
  created_at: '2026-09-30T00:00:00Z',
  title: null,
  allow_original: true,
}

describe('toLineageResponse', () => {
  it('Asset と Run をノードにし、Run の深さは作った画像の1つ上にする', () => {
    const data: PublicShareResponse = {
      root_asset_id: 'b',
      scope: 'ancestors',
      allow_original: true,
      created_at: '2026-09-30T00:00:00Z',
      assets: [
        { ...base, id: 'a', kind: 'generated', run_id: 'r0', depth: -2 },
        { ...base, id: 'b', kind: 'generated', run_id: 'r1', depth: 0 },
      ],
      runs: [
        { id: 'r0', operation: 'generate', model: 'm', prompt: 'p0', params: {}, created_at: '2026-09-30T00:00:00Z' },
        { id: 'r1', operation: 'edit', model: 'm', prompt: 'p1', params: {}, created_at: '2026-09-30T00:00:00Z' },
      ],
      edges: [
        { source: 'r0', target: 'a', kind: 'output', primary: false },
        { source: 'a', target: 'r1', kind: 'input', role: 'image', position: 0, primary: true },
        { source: 'r1', target: 'b', kind: 'output', primary: false },
        { source: 'x', target: 'b', kind: 'origin', primary: false },
      ],
    }
    const result = toLineageResponse(data)
    const depth = Object.fromEntries(result.nodes!.map((n) => [n.id, n.depth]))
    expect(depth).toEqual({ a: -2, b: 0, r0: -3, r1: -1 })
    expect(result.nodes!.find((n) => n.id === 'r1')?.run?.status).toBe('succeeded')
    // 端点の欠けた辺は落とす。
    expect(result.edges).toHaveLength(3)
  })
})

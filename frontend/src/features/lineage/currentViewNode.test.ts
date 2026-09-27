import { describe, expect, it } from 'vitest'
import { deriveCurrentViewNode } from './currentViewNode'

describe('deriveCurrentViewNode', () => {
  it('/assets/:id を asset として判定する', () => {
    expect(deriveCurrentViewNode('/assets/abc-123')).toEqual({ id: 'abc-123', type: 'asset' })
  })

  it('/runs/:id を run として判定する', () => {
    expect(deriveCurrentViewNode('/runs/run-9')).toEqual({ id: 'run-9', type: 'run' })
  })

  it('関係ないパスは null', () => {
    expect(deriveCurrentViewNode('/studio')).toBeNull()
    expect(deriveCurrentViewNode('/lineage/abc')).toBeNull()
    expect(deriveCurrentViewNode('/')).toBeNull()
  })

  it('/studio ではスタジオに表示中の Asset を現在地とする', () => {
    expect(deriveCurrentViewNode('/studio', 'as-1')).toEqual({ id: 'as-1', type: 'asset' })
    expect(deriveCurrentViewNode('/studio', null)).toBeNull()
  })

  it('スタジオ以外では表示中の Asset を使わない(URL が優先)', () => {
    expect(deriveCurrentViewNode('/', 'as-1')).toBeNull()
    expect(deriveCurrentViewNode('/assets/abc', 'as-1')).toEqual({ id: 'abc', type: 'asset' })
  })
})

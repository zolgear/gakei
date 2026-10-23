/**
 * 履歴の節分け(日・グループ・似た順)と、画像単位の項目の組み立て(ADR-0054)。
 */
import { describe, expect, it, vi } from 'vitest'
import {
  daySections,
  formatDayHeading,
  groupSections,
  similaritySections,
  type HistorySection,
} from './historySections'

// 日と年の境目は利用者の時間帯で決まるので、テストではその時間帯の正午で作る。
function at(year: number, month: number, day: number): string {
  return new Date(year, month - 1, day, 12, 0, 0).toISOString()
}

function at2(year: number, month: number, day: number, hour: number, minute = 0): string {
  return new Date(year, month - 1, day, hour, minute, 0).toISOString()
}

function run(id: string, queuedAt: string, assets: string[] = [`${id}-a`]) {
  return { id, queued_at: queuedAt, outputs: assets.map((asset_id) => ({ asset_id })) }
}

/** 節を「[見出し] カードの key...」の配列にする。 */
function outline<R>(sections: HistorySection<R>[]): string[][] {
  return sections.map((s) => [
    s.heading ? `[${s.heading.title}${s.heading.count != null ? `:${s.heading.count}` : ''}]` : '[]',
    ...s.cards.map((c) => c.key),
  ])
}

describe('daySections', () => {
  it('日が変わる位置(最初の日を含む)で節に分ける。今年でない日だけ見出しに年を入れる', () => {
    const cards = [
      { key: 'r4', run: run('r4', at(2027, 1, 2)), outputIndex: 0 },
      { key: 'r3', run: run('r3', at(2026, 12, 31)), outputIndex: 0 },
      { key: 'r2', run: run('r2', at(2026, 12, 31)), outputIndex: 0 },
      { key: 'r1', run: run('r1', at(2026, 6, 1)), outputIndex: 0 },
      { key: 'r0', run: run('r0', at(2025, 3, 3)), outputIndex: 0 },
    ]
    const sections = daySections(cards, 'ja-JP', 2026)
    expect(outline(sections)).toEqual([
      ['[2027年1月2日(土)]', 'r4'],
      ['[12月31日(木)]', 'r3', 'r2'],
      ['[6月1日(月)]', 'r1'],
      ['[2025年3月3日(月)]', 'r0'],
    ])
    expect(sections[0].key).toBe('day:2027-1-2')
    expect(sections[0].heading).toEqual({ title: '2027年1月2日(土)', count: null })
    expect(sections[0].cards[0]).toEqual({ kind: 'card', key: 'r4', run: cards[0].run, outputIndex: 0 })
  })

  it('日の境目は利用者の時間帯の0時', () => {
    const cards = [
      { key: 'b', run: run('b', at2(2026, 10, 24, 0, 30)), outputIndex: 0 },
      { key: 'a', run: run('a', at2(2026, 10, 23, 23, 30)), outputIndex: 0 },
      { key: 'z', run: run('z', at2(2026, 10, 23, 0, 30)), outputIndex: 0 },
    ]
    expect(outline(daySections(cards, 'ja-JP', 2026))).toEqual([
      ['[10月24日(土)]', 'b'],
      ['[10月23日(金)]', 'a', 'z'],
    ])
  })

  it('時間帯が変わると、同じ時刻でも日が変わりうる', () => {
    try {
      // UTC では同じ日(10/23 14:30 と 15:30)だが、東京では 23:30 と翌 0:30。
      const cards = [
        { key: 'b', run: run('b', '2026-10-23T15:30:00Z'), outputIndex: 0 },
        { key: 'a', run: run('a', '2026-10-23T14:30:00Z'), outputIndex: 0 },
      ]
      vi.stubEnv('TZ', 'Asia/Tokyo')
      expect(outline(daySections(cards, 'ja-JP', 2026))).toEqual([
        ['[10月24日(土)]', 'b'],
        ['[10月23日(金)]', 'a'],
      ])
      vi.stubEnv('TZ', 'UTC')
      expect(outline(daySections(cards, 'ja-JP', 2026))).toEqual([['[10月23日(金)]', 'b', 'a']])
    } finally {
      vi.unstubAllEnvs()
    }
  })

  it('空なら節も無い', () => {
    expect(daySections([], 'ja-JP', 2026)).toEqual([])
  })
})

describe('formatDayHeading', () => {
  it('ロケールに合わせ、今年でなければ年を入れる', () => {
    expect(formatDayHeading(at(2026, 10, 22), 'ja-JP', 2026)).toBe('10月22日(木)')
    expect(formatDayHeading(at(2026, 10, 22), 'en-US', 2026)).toBe('Thu, Oct 22')
    expect(formatDayHeading(at(2025, 3, 3), 'ja-JP', 2026)).toBe('2025年3月3日(月)')
    expect(formatDayHeading(at(2025, 3, 3), 'en-US', 2026)).toBe('Mon, Mar 3, 2025')
  })
})

describe('groupSections', () => {
  const groups = [
    { id: 'g1', name: 'Alpha', count: 2 },
    { id: 'g2', name: 'Beta', count: 1 },
    { id: null, name: null, count: 1 },
  ]
  const r1 = run('r1', at(2026, 1, 1), ['a1', 'a2'])
  const r2 = run('r2', at(2026, 1, 2), ['b1'])
  const r3 = run('r3', at(2026, 1, 3), ['c1'])

  it('グループが変わる位置で節に分け、名前と件数を見出しにする。グループなしは最後のラベル', () => {
    const sections = groupSections(
      [
        { asset_id: 'a1', group_id: 'g1', run: r1 },
        { asset_id: 'a2', group_id: 'g1', run: r1 },
        { asset_id: 'b1', group_id: 'g2', run: r2 },
        { asset_id: 'c1', group_id: null, run: r3 },
      ],
      groups,
      'No group',
    )
    expect(outline(sections)).toEqual([
      ['[Alpha:2]', 'r1:a1', 'r1:a2'],
      ['[Beta:1]', 'r2:b1'],
      ['[No group:1]', 'r3:c1'],
    ])
    // 出力の番号は Run の出力の中の位置。
    expect(sections[0].cards[1]).toMatchObject({ kind: 'card', outputIndex: 1 })
  })

  it('ページをまたいで同じグループが続いても、節は1つ', () => {
    const page1 = [{ asset_id: 'a1', group_id: 'g1', run: r1 }]
    const page2 = [{ asset_id: 'a2', group_id: 'g1', run: r1 }]
    expect(groupSections([...page1, ...page2], groups, 'No group')).toHaveLength(1)
  })

  it('応答に節が無ければ件数は null', () => {
    const sections = groupSections([{ asset_id: 'a1', group_id: 'g9', run: r1 }], [], 'No group')
    expect(sections[0].key).toBe('group:g9')
    expect(sections[0].heading).toEqual({ title: '', count: null })
  })
})

describe('similaritySections', () => {
  it('見出しの無い1つの節にし、同じ画像は1回だけ', () => {
    const r1 = run('r1', at(2026, 1, 1), ['a1', 'a2'])
    const sections = similaritySections([
      { asset_id: 'a2', run: r1 },
      { asset_id: 'a1', run: r1 },
      { asset_id: 'a2', run: r1 },
    ])
    expect(sections).toHaveLength(1)
    expect(sections[0].heading).toBeNull()
    expect(sections[0].cards.map((c) => [c.key, c.outputIndex])).toEqual([
      ['r1:a2', 1],
      ['r1:a1', 0],
    ])
  })

  it('空なら節も無い', () => {
    expect(similaritySections([])).toEqual([])
  })
})

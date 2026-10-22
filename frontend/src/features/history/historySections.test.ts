/**
 * 履歴の区切り(年・グループ)と、画像単位の項目の組み立て(ADR-0054)。
 */
import { describe, expect, it } from 'vitest'
import { formatMonthDay, localYear, similarityCards, withGroupBanners, withYearBanners } from './historySections'

// 年の境目は利用者の時間帯で決まるので、テストではその時間帯の正午で作る。
function at(year: number, month: number, day: number): string {
  return new Date(year, month - 1, day, 12, 0, 0).toISOString()
}

function run(id: string, queuedAt: string, assets: string[] = [`${id}-a`]) {
  return { id, queued_at: queuedAt, outputs: assets.map((asset_id) => ({ asset_id })) }
}

describe('withYearBanners', () => {
  it('各年の最初(最初の年を含む)に年のバナーを置く', () => {
    const cards = [
      { key: 'r3', run: run('r3', at(2027, 1, 2)), outputIndex: 0 },
      { key: 'r2', run: run('r2', at(2026, 12, 31)), outputIndex: 0 },
      { key: 'r1', run: run('r1', at(2026, 6, 1)), outputIndex: 0 },
      { key: 'r0', run: run('r0', at(2025, 3, 3)), outputIndex: 0 },
    ]
    const items = withYearBanners(cards)
    expect(items.map((i) => (i.kind === 'banner' ? `[${i.title}]` : i.key))).toEqual([
      '[2027]',
      'r3',
      '[2026]',
      'r2',
      'r1',
      '[2025]',
      'r0',
    ])
    expect(items[0]).toEqual({ kind: 'banner', key: 'year:2027', title: '2027', count: null })
    expect(items[1]).toMatchObject({ kind: 'card', showDate: true })
  })

  it('空なら空', () => {
    expect(withYearBanners([])).toEqual([])
  })
})

describe('localYear と formatMonthDay', () => {
  it('利用者の時間帯での年と月日', () => {
    expect(localYear(at(2026, 10, 23))).toBe(2026)
    expect(formatMonthDay(at(2026, 10, 23))).toBe('10/23')
    expect(formatMonthDay(at(2026, 1, 5))).toBe('1/5')
  })
})

describe('withGroupBanners', () => {
  const groups = [
    { id: 'g1', name: 'Alpha', count: 2 },
    { id: 'g2', name: 'Beta', count: 1 },
    { id: null, name: null, count: 1 },
  ]
  const r1 = run('r1', at(2026, 1, 1), ['a1', 'a2'])
  const r2 = run('r2', at(2026, 1, 2), ['b1'])
  const r3 = run('r3', at(2026, 1, 3), ['c1'])

  it('グループが変わる位置に、名前と件数のバナーを置く。グループなしは最後のラベル', () => {
    const items = withGroupBanners(
      [
        { asset_id: 'a1', group_id: 'g1', run: r1 },
        { asset_id: 'a2', group_id: 'g1', run: r1 },
        { asset_id: 'b1', group_id: 'g2', run: r2 },
        { asset_id: 'c1', group_id: null, run: r3 },
      ],
      groups,
      'No group',
    )
    expect(items.map((i) => (i.kind === 'banner' ? `[${i.title}:${i.count}]` : i.key))).toEqual([
      '[Alpha:2]',
      'r1:a1',
      'r1:a2',
      '[Beta:1]',
      'r2:b1',
      '[No group:1]',
      'r3:c1',
    ])
    // 出力の番号は Run の出力の中の位置。月日は出さない。
    expect(items[2]).toMatchObject({ kind: 'card', outputIndex: 1, showDate: false })
  })

  it('ページをまたいで同じグループが続いても、バナーは1つ', () => {
    const page1 = [{ asset_id: 'a1', group_id: 'g1', run: r1 }]
    const page2 = [{ asset_id: 'a2', group_id: 'g1', run: r1 }]
    const items = withGroupBanners([...page1, ...page2], groups, 'No group')
    expect(items.filter((i) => i.kind === 'banner')).toHaveLength(1)
  })

  it('応答に節が無ければ件数は null', () => {
    const items = withGroupBanners([{ asset_id: 'a1', group_id: 'g9', run: r1 }], [], 'No group')
    expect(items[0]).toEqual({ kind: 'banner', key: 'group:g9', title: '', count: null })
  })
})

describe('similarityCards', () => {
  it('区切りを置かず、同じ画像は1回だけ', () => {
    const r1 = run('r1', at(2026, 1, 1), ['a1', 'a2'])
    const items = similarityCards([
      { asset_id: 'a2', run: r1 },
      { asset_id: 'a1', run: r1 },
      { asset_id: 'a2', run: r1 },
    ])
    expect(items.map((i) => (i.kind === 'card' ? [i.key, i.outputIndex] : null))).toEqual([
      ['r1:a2', 1],
      ['r1:a1', 0],
    ])
  })
})

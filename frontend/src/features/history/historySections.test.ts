/**
 * 履歴の区切り(日・グループ)と、画像単位の項目の組み立て(ADR-0054)。
 */
import { describe, expect, it, vi } from 'vitest'
import {
  formatMonthDay,
  localYear,
  similarityCards,
  withDayBanners,
  withGroupBanners,
  type HistoryListItem,
} from './historySections'

// 年の境目は利用者の時間帯で決まるので、テストではその時間帯の正午で作る。
function at(year: number, month: number, day: number): string {
  return new Date(year, month - 1, day, 12, 0, 0).toISOString()
}

function run(id: string, queuedAt: string, assets: string[] = [`${id}-a`]) {
  return { id, queued_at: queuedAt, outputs: assets.map((asset_id) => ({ asset_id })) }
}

function at2(year: number, month: number, day: number, hour: number, minute = 0): string {
  return new Date(year, month - 1, day, hour, minute, 0).toISOString()
}

const label = (i: HistoryListItem<ReturnType<typeof run>>) =>
  i.kind === 'banner' ? `[${i.eyebrow ? `${i.eyebrow} ` : ''}${i.title}]` : i.key

describe('withDayBanners', () => {
  it('日が変わる位置(最初の日を含む)に日のバナーを置き、年は各年の最初の日のバナーにだけ添える', () => {
    const cards = [
      { key: 'r4', run: run('r4', at(2027, 1, 2)), outputIndex: 0 },
      { key: 'r3', run: run('r3', at(2026, 12, 31)), outputIndex: 0 },
      { key: 'r2', run: run('r2', at(2026, 12, 31)), outputIndex: 0 },
      { key: 'r1', run: run('r1', at(2026, 6, 1)), outputIndex: 0 },
      { key: 'r0', run: run('r0', at(2025, 3, 3)), outputIndex: 0 },
    ]
    const items = withDayBanners(cards, 'ja-JP')
    expect(items.map(label)).toEqual([
      '[2027 1/2]',
      'r4',
      '[2026 12/31]',
      'r3',
      'r2',
      '[6/1]',
      'r1',
      '[2025 3/3]',
      'r0',
    ])
    // 年のバナーと日のバナーを2枚続けない(バナーの数は日の数と同じ)。
    expect(items.filter((i) => i.kind === 'banner')).toHaveLength(4)
    expect(items[0]).toEqual({
      kind: 'banner',
      key: 'day:2027-1-2',
      title: '1/2',
      count: null,
      eyebrow: '2027',
      caption: '土',
    })
    expect(items[1]).toEqual({ kind: 'card', key: 'r4', run: cards[0].run, outputIndex: 0 })
  })

  it('日の境目は利用者の時間帯の0時', () => {
    const cards = [
      { key: 'b', run: run('b', at2(2026, 10, 24, 0, 30)), outputIndex: 0 },
      { key: 'a', run: run('a', at2(2026, 10, 23, 23, 30)), outputIndex: 0 },
      { key: 'z', run: run('z', at2(2026, 10, 23, 0, 30)), outputIndex: 0 },
    ]
    expect(withDayBanners(cards, 'ja-JP').map(label)).toEqual(['[2026 10/24]', 'b', '[10/23]', 'a', 'z'])
  })

  it('時間帯が変わると、同じ時刻でも日が変わりうる', () => {
    try {
      // UTC では同じ日(10/23 14:30 と 15:30)だが、東京では 23:30 と翌 0:30。
      const cards = [
        { key: 'b', run: run('b', '2026-10-23T15:30:00Z'), outputIndex: 0 },
        { key: 'a', run: run('a', '2026-10-23T14:30:00Z'), outputIndex: 0 },
      ]
      vi.stubEnv('TZ', 'Asia/Tokyo')
      expect(withDayBanners(cards, 'ja-JP').map(label)).toEqual(['[2026 10/24]', 'b', '[10/23]', 'a'])
      vi.stubEnv('TZ', 'UTC')
      expect(withDayBanners(cards, 'ja-JP').map(label)).toEqual(['[2026 10/23]', 'b', 'a'])
    } finally {
      vi.unstubAllEnvs()
    }
  })

  it('曜日はロケールに合わせる', () => {
    const cards = [{ key: 'a', run: run('a', at(2026, 10, 22)), outputIndex: 0 }]
    expect(withDayBanners(cards, 'ja-JP')[0]).toMatchObject({ caption: '木' })
    expect(withDayBanners(cards, 'en-US')[0]).toMatchObject({ caption: 'Thu' })
  })

  it('空なら空', () => {
    expect(withDayBanners([], 'ja-JP')).toEqual([])
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
    // 出力の番号は Run の出力の中の位置。
    expect(items[2]).toMatchObject({ kind: 'card', outputIndex: 1 })
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

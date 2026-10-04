import { describe, expect, it } from 'vitest'
import {
  normalizeStockKindFilter,
  stockAssetsQueryKey,
  stockGroupCountKinds,
  stockKindChoices,
  stockKindsKey,
  stockListKinds,
  stockPickerKindChoices,
  stockPickerKinds,
  stockPickerQueryKey,
} from './stockQueryKey'

describe('stockAssetsQueryKey', () => {
  it('グループの節はグループ id を含む', () => {
    expect(stockAssetsQueryKey(['generated'], { groupId: 'g1' })).toEqual(['assets', 'generated', 'group', 'g1'])
  })

  it('「グループなし」の節は ungrouped。種類を省いたときは all', () => {
    expect(stockAssetsQueryKey(null, { ungrouped: true })).toEqual(['assets', 'all', 'ungrouped'])
  })

  it('グループ id が "ungrouped" でも「グループなし」の節とは衝突しない', () => {
    expect(stockAssetsQueryKey(null, { groupId: 'ungrouped' })).not.toEqual(
      stockAssetsQueryKey(null, { ungrouped: true }),
    )
  })

  it('種類の集合は順序に依らず同じキーになり、違う集合とは別のキーになる', () => {
    expect(stockAssetsQueryKey(['upload', 'generated'], { ungrouped: true })).toEqual(
      stockAssetsQueryKey(['generated', 'upload'], { ungrouped: true }),
    )
    expect(stockAssetsQueryKey(['generated', 'upload'], { ungrouped: true })).toEqual([
      'assets',
      'generated,upload',
      'ungrouped',
    ])
    expect(stockAssetsQueryKey(['generated', 'upload'], { ungrouped: true })).not.toEqual(
      stockAssetsQueryKey(null, { ungrouped: true }),
    )
  })

  it('["assets"] で始まる(invalidateQueries でまとめて作り直せる)', () => {
    expect(stockAssetsQueryKey(['upload'], { ungrouped: true })[0]).toBe('assets')
  })
})

describe('stockAssetsQueryKey(タグの絞り込み)', () => {
  it('タグで絞ると末尾に tag を足す', () => {
    expect(stockAssetsQueryKey(null, { ungrouped: true }, 'cat')).toEqual(['assets', 'all', 'ungrouped', 'tag', 'cat'])
    expect(stockAssetsQueryKey(['generated'], { groupId: 'g1' }, 'cat')).toEqual([
      'assets',
      'generated',
      'group',
      'g1',
      'tag',
      'cat',
    ])
  })

  it('null や空文字は絞り込みなしと同じキー', () => {
    expect(stockAssetsQueryKey(null, { ungrouped: true }, null)).toEqual(['assets', 'all', 'ungrouped'])
    expect(stockAssetsQueryKey(null, { ungrouped: true }, '')).toEqual(['assets', 'all', 'ungrouped'])
  })

  it('タグが違えば別のキー', () => {
    expect(stockAssetsQueryKey(null, { groupId: 'g1' }, 'cat')).not.toEqual(
      stockAssetsQueryKey(null, { groupId: 'g1' }, 'dog'),
    )
  })
})

describe('ストックのパネルの種類(ADR-0035)', () => {
  it('設定がオフならチップからスケッチとマスクを外す', () => {
    expect(stockKindChoices(false)).toEqual(['all', 'generated', 'upload'])
  })

  it('設定がオンならチップは 5 つ', () => {
    expect(stockKindChoices(true)).toEqual(['all', 'generated', 'upload', 'sketch', 'mask'])
  })

  it('スケッチ・マスクを選んだまま設定をオフにすると「すべて」に戻す', () => {
    expect(normalizeStockKindFilter('sketch', false)).toBe('all')
    expect(normalizeStockKindFilter('mask', false)).toBe('all')
    expect(normalizeStockKindFilter('generated', false)).toBe('generated')
    expect(normalizeStockKindFilter('mask', true)).toBe('mask')
  })

  it('設定がオフの「すべて」は生成画像とアップロードで取る', () => {
    expect(stockListKinds('all', false)).toEqual(['generated', 'upload'])
  })

  it('設定がオンの「すべて」は種類を省く', () => {
    expect(stockListKinds('all', true)).toBeNull()
  })

  it('1 つの種類を選べばその種類だけで取る', () => {
    expect(stockListKinds('upload', false)).toEqual(['upload'])
    expect(stockListKinds('sketch', true)).toEqual(['sketch'])
  })

  it('出せない種類を渡されたら「すべて」と同じに取る', () => {
    expect(stockListKinds('mask', false)).toEqual(['generated', 'upload'])
  })

  it('グループの件数は設定だけで決まる(オフなら生成画像とアップロード、オンなら省く)', () => {
    expect(stockGroupCountKinds(false)).toEqual(['generated', 'upload'])
    expect(stockGroupCountKinds(true)).toBeNull()
  })
})

describe('ストックピッカーの種類(ADR-0035)', () => {
  it('マスクのチップは出さず、スケッチは設定に従う', () => {
    expect(stockPickerKindChoices(false)).toEqual(['all', 'generated', 'upload'])
    expect(stockPickerKindChoices(true)).toEqual(['all', 'generated', 'upload', 'sketch'])
  })

  it('「すべて」でもマスクを除くため、常に種類を明示する', () => {
    expect(stockPickerKinds('all', false)).toEqual(['generated', 'upload'])
    expect(stockPickerKinds('all', true)).toEqual(['generated', 'upload', 'sketch'])
    expect(stockPickerKinds('sketch', true)).toEqual(['sketch'])
    expect(stockPickerKinds('sketch', false)).toEqual(['generated', 'upload'])
  })

  it('ピッカーのキーはパネルの節のキーと衝突しない', () => {
    const picker = stockPickerQueryKey(['generated', 'upload'])
    expect(picker).toEqual(['assets', 'picker', 'generated,upload'])
    expect(picker).not.toEqual(stockAssetsQueryKey(['generated', 'upload'], { ungrouped: true }).slice(0, 3))
    expect(stockKindsKey(['generated'])).not.toBe('picker')
  })
})

import { afterEach, describe, expect, it } from 'vitest'
import { STOCK_SELECTION_SEND_MAX, getStockSelectedAssetIds, setStockSelectedAssetIds } from './stockSelectionStore'

describe('stockSelectionStore', () => {
  afterEach(() => setStockSelectedAssetIds([]))

  it('既定は空で、書き込んだ順に返し、空で戻せる', () => {
    expect(getStockSelectedAssetIds()).toEqual([])
    setStockSelectedAssetIds(['b', 'a'])
    expect(getStockSelectedAssetIds()).toEqual(['b', 'a'])
    setStockSelectedAssetIds([])
    expect(getStockSelectedAssetIds()).toEqual([])
  })

  it('送る上限で切る', () => {
    const ids = Array.from({ length: STOCK_SELECTION_SEND_MAX + 5 }, (_, i) => `a${i}`)
    setStockSelectedAssetIds(ids)
    expect(getStockSelectedAssetIds()).toHaveLength(STOCK_SELECTION_SEND_MAX)
    expect(getStockSelectedAssetIds()[0]).toBe('a0')
  })

  it('書き込んだ配列を後で変えても影響しない', () => {
    const ids = ['a']
    setStockSelectedAssetIds(ids)
    ids.push('b')
    expect(getStockSelectedAssetIds()).toEqual(['a'])
  })
})

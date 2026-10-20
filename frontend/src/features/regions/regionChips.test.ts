import { describe, expect, it } from 'vitest'
import { messagesFor } from '../../i18n'
import { labelChipItems, regionChipItems } from './regionChips'

describe('regionChipItems', () => {
  it('番号の順に「四角 n」を作り、名前があれば添える(入れるのは「四角 n」だけ)', () => {
    const items = regionChipItems(
      messagesFor('ja'),
      [
        { number: 2, kind: 'add', box: [0, 0, 0.5, 0.5], instruction: 'x', label: ' カップ ' },
        { number: 1, kind: 'change', box: [0, 0, 0.5, 0.5], instruction: 'y' },
      ],
    )
    expect(items.map((i) => [i.text, i.display, i.colorIndex])).toEqual([
      ['四角 1', '四角 1', 0],
      ['四角 2', '四角 2 カップ', 1],
    ])
    expect(items[1].highlight).toEqual({ type: 'region', number: 2 })
  })
})

describe('labelChipItems', () => {
  it('同じ名前は1つにし、空の名前は捨てる', () => {
    const items = labelChipItems([{ label: 'カップ' }, { label: ' ' }, { label: 'カップ' }, { label: '窓' }])
    expect(items.map((i) => i.text)).toEqual(['カップ', '窓'])
    expect(items[0].highlight).toEqual({ type: 'label', label: 'カップ' })
  })
})

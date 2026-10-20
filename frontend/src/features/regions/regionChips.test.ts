import { describe, expect, it } from 'vitest'
import { messagesFor } from '../../i18n'
import { candidateChipItems, labelChipItems, regionChipItems } from './regionChips'

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

describe('candidateChipItems', () => {
  it('四角にした候補は出さず、残りは送られない候補として出す', () => {
    const items = candidateChipItems(
      [
        { key: 'd:0', label: '左のカバン', box: [0.1, 0.2, 0.3, 0.4] },
        { key: 'd:1', label: '中央のカバン', box: [0.4, 0.2, 0.6, 0.4] },
        { key: 'd:2', label: '中央のカバン', box: [0.4, 0.2, 0.6, 0.4] },
      ],
      [{ number: 1, kind: 'change', box: [0.1, 0.2, 0.3, 0.4], instruction: '', label: '左のカバン' }],
    )
    expect(items.map((i) => i.text)).toEqual(['中央のカバン'])
    expect(items[0].candidate?.key).toBe('d:1')
  })
})

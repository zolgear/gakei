import { describe, expect, it } from 'vitest'
import {
  addImageInputs,
  moveImageInput,
  removeImageInput,
  removeMaskInput,
  replaceImageInput,
  replaceImageInputs,
  setMaskInput,
} from './editInputs'
import type { RunInputItem } from './types'

const img = (assetId: string, position: number): RunInputItem => ({
  assetId,
  role: 'image',
  position,
})
const mask = (assetId: string): RunInputItem => ({ assetId, role: 'mask', position: 0 })

describe('addImageInputs', () => {
  it('空の状態に追加すると position 0 から振られる', () => {
    const result = addImageInputs([], ['a', 'b'], 16)
    expect(result.inputs).toEqual([img('a', 0), img('b', 1)])
    expect(result.addedCount).toBe(2)
    expect(result.rejectedCount).toBe(0)
  })

  it('上限を超える分は追加されない', () => {
    const result = addImageInputs([img('a', 0)], ['b', 'c', 'd'], 2)
    expect(result.inputs).toEqual([img('a', 0), img('b', 1)])
    expect(result.addedCount).toBe(1)
    expect(result.rejectedCount).toBe(2)
  })

  it('マスクがあれば末尾に保持したまま追加する', () => {
    const result = addImageInputs([img('a', 0), mask('m')], ['b'], 16)
    expect(result.inputs).toEqual([img('a', 0), img('b', 1), mask('m')])
  })
})

describe('removeImageInput', () => {
  it('末尾を削除しても position 0 は変わらないのでマスクは残る', () => {
    const result = removeImageInput([img('a', 0), img('b', 1), mask('m')], 'b')
    expect(result.inputs).toEqual([img('a', 0), mask('m')])
    expect(result.maskDropped).toBe(false)
  })

  it('position 0 を削除すると残りが振り直され、マスクは外れる', () => {
    const result = removeImageInput([img('a', 0), img('b', 1), mask('m')], 'a')
    expect(result.inputs).toEqual([img('b', 0)])
    expect(result.maskDropped).toBe(true)
  })

  it('マスクが無ければ maskDropped は false', () => {
    const result = removeImageInput([img('a', 0)], 'a')
    expect(result.inputs).toEqual([])
    expect(result.maskDropped).toBe(false)
  })
})

describe('moveImageInput', () => {
  it('up で1つ前と入れ替わる', () => {
    const result = moveImageInput([img('a', 0), img('b', 1), img('c', 2)], 'b', 'up')
    expect(result.inputs).toEqual([img('b', 0), img('a', 1), img('c', 2)])
  })

  it('down で1つ後ろと入れ替わる', () => {
    const result = moveImageInput([img('a', 0), img('b', 1), img('c', 2)], 'b', 'down')
    expect(result.inputs).toEqual([img('a', 0), img('c', 1), img('b', 2)])
  })

  it('先頭を up しても変化しない', () => {
    const result = moveImageInput([img('a', 0), img('b', 1)], 'a', 'up')
    expect(result.inputs).toEqual([img('a', 0), img('b', 1)])
    expect(result.maskDropped).toBe(false)
  })

  it('末尾を down しても変化しない', () => {
    const result = moveImageInput([img('a', 0), img('b', 1)], 'b', 'down')
    expect(result.inputs).toEqual([img('a', 0), img('b', 1)])
  })

  it('position 0 が入れ替わるとマスクが外れる', () => {
    const result = moveImageInput([img('a', 0), img('b', 1), mask('m')], 'b', 'up')
    expect(result.inputs).toEqual([img('b', 0), img('a', 1)])
    expect(result.maskDropped).toBe(true)
  })

  it('position 0 が変わらない並べ替えではマスクは残る', () => {
    const result = moveImageInput([img('a', 0), img('b', 1), img('c', 2), mask('m')], 'c', 'up')
    expect(result.inputs).toEqual([img('a', 0), img('c', 1), img('b', 2), mask('m')])
    expect(result.maskDropped).toBe(false)
  })
})

describe('setMaskInput / removeMaskInput', () => {
  it('setMaskInput は既存のマスクを置き換える', () => {
    const result = setMaskInput([img('a', 0), mask('old')], 'new')
    expect(result).toEqual([img('a', 0), mask('new')])
  })

  it('removeMaskInput はマスクだけ取り除く', () => {
    const result = removeMaskInput([img('a', 0), mask('m')])
    expect(result).toEqual([img('a', 0)])
  })
})

describe('replaceImageInputs', () => {
  it('画像1枚だけの position 0 を返す(マスクも含まない)', () => {
    expect(replaceImageInputs('new-asset')).toEqual([img('new-asset', 0)])
  })
})

describe('replaceImageInput', () => {
  it('同じ position のまま assetId を差し替える', () => {
    const result = replaceImageInput([img('a', 0), img('b', 1)], 'b', 'c')
    expect(result).toEqual([img('a', 0), img('c', 1)])
  })

  it('position 0 を差し替えてもマスクはそのまま残る', () => {
    const result = replaceImageInput([img('a', 0), img('b', 1), mask('m')], 'a', 'z')
    expect(result).toEqual([img('z', 0), img('b', 1), mask('m')])
  })

  it('from が見つからなければ末尾に追加する', () => {
    const result = replaceImageInput([img('a', 0)], 'missing', 'z')
    expect(result).toEqual([img('a', 0), img('z', 1)])
  })
})

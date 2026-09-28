import { describe, expect, it } from 'vitest'
import {
  addImageInputs,
  ensureInputIds,
  inputsFromRunInputs,
  moveImageInput,
  newInputId,
  removeImageInput,
  removeMaskInput,
  replaceImageInput,
  replaceImageInputs,
  setMaskInput,
} from './editInputs'
import type { RunInputItem } from './types'

// 既存のテストを読みやすく保つため、inputId は省略時に assetId と同じ値にする
// (同じ画像を複数回入れるテストでは明示的に別の inputId を渡す)。
const img = (assetId: string, position: number, inputId: string = assetId): RunInputItem => ({
  inputId,
  assetId,
  role: 'image',
  position,
})
const mask = (assetId: string, inputId: string = `mask-${assetId}`): RunInputItem => ({
  inputId,
  assetId,
  role: 'mask',
  position: 0,
})

/** 採番される inputId を除いた形(assetId・role・position)だけを比べる。 */
const shape = (inputs: RunInputItem[]) =>
  inputs.map((i) => ({ assetId: i.assetId, role: i.role, position: i.position }))
const shapeImg = (assetId: string, position: number) => ({ assetId, role: 'image', position })
const shapeMask = (assetId: string) => ({ assetId, role: 'mask', position: 0 })

describe('newInputId', () => {
  it('呼ぶたびに別の値を返す', () => {
    const ids = new Set(Array.from({ length: 50 }, () => newInputId()))
    expect(ids.size).toBe(50)
  })
})

describe('addImageInputs', () => {
  it('空の状態に追加すると position 0 から振られる', () => {
    const result = addImageInputs([], ['a', 'b'], 16)
    expect(shape(result.inputs)).toEqual([shapeImg('a', 0), shapeImg('b', 1)])
    expect(result.addedCount).toBe(2)
    expect(result.rejectedCount).toBe(0)
  })

  it('上限を超える分は追加されない', () => {
    const result = addImageInputs([img('a', 0)], ['b', 'c', 'd'], 2)
    expect(result.inputs[0]).toEqual(img('a', 0))
    expect(shape(result.inputs)).toEqual([shapeImg('a', 0), shapeImg('b', 1)])
    expect(result.addedCount).toBe(1)
    expect(result.rejectedCount).toBe(2)
  })

  it('マスクがあれば末尾に保持したまま追加する', () => {
    const result = addImageInputs([img('a', 0), mask('m')], ['b'], 16)
    expect(shape(result.inputs)).toEqual([shapeImg('a', 0), shapeImg('b', 1), shapeMask('m')])
    expect(result.inputs[2]).toEqual(mask('m'))
  })

  it('同じ assetId を2回追加すると、別々の inputId を持つ2件になる(#12)', () => {
    const first = addImageInputs([], ['a'], 16)
    const second = addImageInputs(first.inputs, ['a'], 16)
    expect(shape(second.inputs)).toEqual([shapeImg('a', 0), shapeImg('a', 1)])
    expect(second.inputs[0].inputId).not.toBe(second.inputs[1].inputId)
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

  it('同じ画像を2回追加して片方を消すと、もう片方が position 0 で1件だけ残る(#12)', () => {
    const added = addImageInputs(addImageInputs([], ['a'], 16).inputs, ['a'], 16).inputs
    const [first, second] = added

    const removeSecond = removeImageInput(added, second.inputId)
    expect(removeSecond.inputs).toEqual([{ ...first, position: 0 }])

    const removeFirst = removeImageInput(added, first.inputId)
    expect(removeFirst.inputs).toEqual([{ ...second, position: 0 }])
  })

  it('同じ画像2回+別の画像で No.2 を消しても壊れず、最後まで消せる(#13)', () => {
    // No.1 青(A)、No.2 青(A)、No.3 灰色(B)
    let inputs = addImageInputs([], ['A'], 16).inputs
    inputs = addImageInputs(inputs, ['A'], 16).inputs
    inputs = addImageInputs(inputs, ['B'], 16).inputs
    const [a1, a2, b] = inputs

    // No.2 を消す → [A pos 0, B pos 1]
    inputs = removeImageInput(inputs, a2.inputId).inputs
    expect(inputs).toEqual([
      { ...a1, position: 0 },
      { ...b, position: 1 },
    ])

    // 灰色(B)を消す → [A pos 0]
    inputs = removeImageInput(inputs, b.inputId).inputs
    expect(inputs).toEqual([{ ...a1, position: 0 }])

    // 最後の A を消す → 空(消えたものが復活しない)
    inputs = removeImageInput(inputs, a1.inputId).inputs
    expect(inputs).toEqual([])
  })

  it('存在しない inputId なら何も消さない', () => {
    const result = removeImageInput([img('a', 0), img('b', 1)], 'missing')
    expect(result.inputs).toEqual([img('a', 0), img('b', 1)])
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

  it('同じ画像が2件あるとき、指定した方だけが動く', () => {
    // [A(x) pos 0, B pos 1, A(y) pos 2] で2件目の A(y) を up → [A(x), A(y), B]
    const inputs = [img('A', 0, 'x'), img('B', 1, 'b'), img('A', 2, 'y')]
    const result = moveImageInput(inputs, 'y', 'up')
    expect(result.inputs).toEqual([img('A', 0, 'x'), img('A', 1, 'y'), img('B', 2, 'b')])

    // 1件目の A(x) を down → [B, A(x), A(y)](2件目の A は動かない)
    const result2 = moveImageInput(inputs, 'x', 'down')
    expect(result2.inputs).toEqual([img('B', 0, 'b'), img('A', 1, 'x'), img('A', 2, 'y')])
  })

  it('同じ画像どうしで position 0 が入れ替わってもマスクは外れる(position 0 の入力が別の1件になるため)', () => {
    const inputs = [img('A', 0, 'x'), img('A', 1, 'y'), mask('m')]
    const result = moveImageInput(inputs, 'y', 'up')
    expect(result.inputs).toEqual([img('A', 0, 'y'), img('A', 1, 'x')])
    expect(result.maskDropped).toBe(true)
  })
})

describe('setMaskInput / removeMaskInput', () => {
  it('setMaskInput は既存のマスクを置き換え、inputId を振る', () => {
    const result = setMaskInput([img('a', 0), mask('old')], 'new')
    expect(shape(result)).toEqual([shapeImg('a', 0), shapeMask('new')])
    expect(result[1].inputId).toEqual(expect.any(String))
    expect(result[1].inputId).not.toBe(result[0].inputId)
  })

  it('removeMaskInput はマスクだけ取り除く', () => {
    const result = removeMaskInput([img('a', 0), mask('m')])
    expect(result).toEqual([img('a', 0)])
  })
})

describe('replaceImageInputs', () => {
  it('画像1枚だけの position 0 を返す(マスクも含まない)', () => {
    const result = replaceImageInputs('new-asset')
    expect(shape(result)).toEqual([shapeImg('new-asset', 0)])
    expect(result[0].inputId).toEqual(expect.any(String))
  })
})

describe('replaceImageInput', () => {
  it('同じ position・同じ inputId のまま assetId を差し替える', () => {
    const result = replaceImageInput([img('a', 0), img('b', 1)], 'b', 'c')
    expect(result).toEqual([img('a', 0), img('c', 1, 'b')])
  })

  it('position 0 を差し替えてもマスクはそのまま残る', () => {
    const result = replaceImageInput([img('a', 0), img('b', 1), mask('m')], 'a', 'z')
    expect(result).toEqual([img('z', 0, 'a'), img('b', 1), mask('m')])
  })

  it('from が見つからなければ末尾に追加する', () => {
    const result = replaceImageInput([img('a', 0)], 'missing', 'z')
    expect(shape(result)).toEqual([shapeImg('a', 0), shapeImg('z', 1)])
  })

  it('同じ画像が2件あるとき、指定した1件だけを差し替える', () => {
    const result = replaceImageInput([img('A', 0, 'x'), img('A', 1, 'y')], 'y', 'Z')
    expect(result).toEqual([img('A', 0, 'x'), img('Z', 1, 'y')])
  })
})

describe('ensureInputIds', () => {
  it('inputId が揃っていれば同じ配列をそのまま返す', () => {
    const inputs = [img('a', 0), img('b', 1), mask('m')]
    expect(ensureInputIds(inputs)).toBe(inputs)
  })

  it('inputId が無い入力(旧形式)には新しい inputId を振る', () => {
    const legacy = [
      { assetId: 'A', role: 'image' as const, position: 0 },
      { assetId: 'A', role: 'image' as const, position: 1 },
    ]
    const result = ensureInputIds(legacy)
    expect(shape(result)).toEqual([shapeImg('A', 0), shapeImg('A', 1)])
    expect(result[0].inputId).toEqual(expect.any(String))
    expect(result[0].inputId).not.toBe(result[1].inputId)
  })

  it('重複した inputId は後の方を振り直す', () => {
    const result = ensureInputIds([img('A', 0, 'dup'), img('B', 1, 'dup')])
    expect(result[0]).toEqual(img('A', 0, 'dup'))
    expect(result[1].inputId).not.toBe('dup')
    expect(shape(result)).toEqual([shapeImg('A', 0), shapeImg('B', 1)])
  })
})

describe('inputsFromRunInputs', () => {
  it('API の run_input から、同じ Asset が複数あっても別々の inputId で作る', () => {
    const result = inputsFromRunInputs([
      { asset_id: 'A', role: 'image', position: 0 },
      { asset_id: 'A', role: 'image', position: 1 },
      { asset_id: 'M', role: 'mask', position: 0 },
    ])
    expect(shape(result)).toEqual([shapeImg('A', 0), shapeImg('A', 1), shapeMask('M')])
    expect(new Set(result.map((i) => i.inputId)).size).toBe(3)
  })
})

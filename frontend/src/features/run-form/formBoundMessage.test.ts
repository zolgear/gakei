import { describe, expect, it } from 'vitest'
import { nextFormBoundMessage, runFormContentKey, type FormBoundMessageState } from './formBoundMessage'

const empty: FormBoundMessageState<string> = { value: null, key: null }
const shown = (value: string): FormBoundMessageState<string> => ({ value, key: null })

const base = {
  provider: 'fake',
  model: 'gpt-image-2',
  prompt: 'a cat',
  rawParams: { quality: 'low' },
  sizeState: { mode: 'auto' },
  inputs: [{ assetId: 'a', role: 'image', position: 0 }],
  regions: [],
  repeatRaw: '1',
  assetGroupId: null,
  promptTemplate: null,
}

describe('nextFormBoundMessage(フォームの内容が変わったら知らせを消す)', () => {
  it('知らせが無ければ何もしない', () => {
    expect(nextFormBoundMessage(empty, 'k1')).toBe(empty)
  })

  it('出した直後の描画で今の内容を覚え、内容が同じ間は残す', () => {
    const adopted = nextFormBoundMessage(shown('seed を外しました'), 'k1')
    expect(adopted).toEqual({ value: 'seed を外しました', key: 'k1' })
    expect(nextFormBoundMessage(adopted, 'k1')).toBe(adopted)
  })

  it('内容が変わったら消す(モデルの切り替えで外した項目の知らせが、次の編集で消える)', () => {
    const adopted = nextFormBoundMessage(shown('seed を外しました'), 'k1')
    expect(nextFormBoundMessage(adopted, 'k2')).toEqual({ value: null, key: null })
  })

  it('消えた後に出した新しい知らせは、その時点の内容を覚え直す', () => {
    const adopted = nextFormBoundMessage(shown('前の知らせ'), 'k1')
    const cleared = nextFormBoundMessage(adopted, 'k2')
    const again = nextFormBoundMessage({ ...cleared, value: '新しい知らせ' }, 'k2')
    expect(again).toEqual({ value: '新しい知らせ', key: 'k2' })
  })
})

describe('runFormContentKey', () => {
  it('同じ内容なら同じキー', () => {
    expect(runFormContentKey(base)).toBe(runFormContentKey({ ...base, inputs: [...base.inputs] }))
  })

  it.each([
    ['プロンプト', { prompt: 'a dog' }],
    ['モデル', { model: 'gpt-image-2.5-flare' }],
    ['プロバイダー', { provider: 'comfyui' }],
    ['パラメーター', { rawParams: { quality: 'high' } }],
    ['サイズ', { sizeState: { mode: 'preset', value: '1024x1024' } }],
    ['入力', { inputs: [] }],
    ['四角', { regions: [{ number: 1 }] }],
    ['繰り返し', { repeatRaw: '2' }],
    ['グループ', { assetGroupId: 'g1' }],
  ])('%s が変わるとキーが変わる', (_label, change) => {
    expect(runFormContentKey({ ...base, ...change })).not.toBe(runFormContentKey(base))
  })

  it('入力の識別子(inputId)だけの違いではキーは変わらない', () => {
    const withIds = { ...base, inputs: [{ inputId: 'x', assetId: 'a', role: 'image', position: 0 }] }
    expect(runFormContentKey(withIds)).toBe(runFormContentKey(base))
  })
})

import { describe, expect, it } from 'vitest'
import {
  comfyuiPromptDownloadFilename,
  extractComfyUiPrompt,
  comfyUiSeedTooltip,
  describeComfyUiSeed,
  extractComfyUiSeed,
  extractComfyUiWorkflowInfo,
  shortSha256,
} from './comfyuiPromptDisplay'

describe('extractComfyUiWorkflowInfo', () => {
  it('id/name/template_sha256 が揃っていれば取り出す', () => {
    const params = { comfyui_workflow: { id: 'w1', name: 'inpaint', template_sha256: 'abc123' } }
    expect(extractComfyUiWorkflowInfo(params)).toEqual({ id: 'w1', name: 'inpaint', template_sha256: 'abc123' })
  })

  it('comfyui_workflow が無ければ null', () => {
    expect(extractComfyUiWorkflowInfo({ quality: 'low' })).toBeNull()
  })

  it('形が合わなければ null', () => {
    expect(extractComfyUiWorkflowInfo({ comfyui_workflow: { id: 'w1' } })).toBeNull()
    expect(extractComfyUiWorkflowInfo({ comfyui_workflow: 'not-an-object' })).toBeNull()
  })
})

describe('extractComfyUiPrompt', () => {
  it('オブジェクトであれば取り出す', () => {
    const graph = { '1': { class_type: 'KSampler', inputs: {} } }
    expect(extractComfyUiPrompt({ comfyui_prompt: graph })).toEqual(graph)
  })

  it('comfyui_prompt が無ければ null', () => {
    expect(extractComfyUiPrompt({})).toBeNull()
  })

  it('配列や非オブジェクトなら null', () => {
    expect(extractComfyUiPrompt({ comfyui_prompt: [1, 2] })).toBeNull()
    expect(extractComfyUiPrompt({ comfyui_prompt: 'x' })).toBeNull()
  })
})

describe('extractComfyUiSeed', () => {
  it('数値であれば取り出す', () => {
    expect(extractComfyUiSeed({ comfyui_seed: 12345 })).toBe(12345)
  })

  it('comfyui_seed が無ければ null', () => {
    expect(extractComfyUiSeed({})).toBeNull()
  })

  it('数値でなければ null', () => {
    expect(extractComfyUiSeed({ comfyui_seed: '12345' })).toBeNull()
  })
})

describe('shortSha256', () => {
  it('既定で先頭12文字にする', () => {
    expect(shortSha256('abcdefghijklmnopqrstuvwxyz')).toBe('abcdefghijkl')
  })

  it('長さを指定できる', () => {
    expect(shortSha256('abcdefghijklmnopqrstuvwxyz', 6)).toBe('abcdef')
  })

  it('元の文字列が短ければそのまま', () => {
    expect(shortSha256('abc')).toBe('abc')
  })
})

describe('comfyuiPromptDownloadFilename', () => {
  it('run id を含んだファイル名にする', () => {
    expect(comfyuiPromptDownloadFilename('11111111-1111-1111-1111-111111111111')).toBe(
      'comfyui-prompt-11111111-1111-1111-1111-111111111111.json',
    )
  })
})

describe('describeComfyUiSeed', () => {
  it('seed が無ければ null', () => {
    expect(describeComfyUiSeed(null, 2, 0)).toBeNull()
  })

  it('1枚なら seed だけ', () => {
    expect(describeComfyUiSeed(42, 1, 0)).toBe('seed 42')
  })

  it('2枚以上で出力を指定すると何枚目かを添える(1始まり)', () => {
    expect(describeComfyUiSeed(42, 3, 1)).toBe('seed 42 ・ 3 枚中 2 枚目')
  })

  it('2枚以上で出力を指定しなければ、まとめて生成したことを示す', () => {
    expect(describeComfyUiSeed(42, 3)).toBe('seed 42 ・ 3 枚を1回で生成')
  })

  it('ツールチップは2枚以上のときだけ', () => {
    expect(comfyUiSeedTooltip(1)).toBeUndefined()
    expect(comfyUiSeedTooltip(2)).toContain('同じ並び')
  })
})

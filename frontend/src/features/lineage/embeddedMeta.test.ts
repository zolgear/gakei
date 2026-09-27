import { describe, expect, it } from 'vitest'
import { describeTool, isC2paUnknown, paramEntries, rawEntries, PROMPT_COLLAPSE_LENGTH } from './embeddedMeta'
import { messagesFor } from '../../i18n'
import type { EmbeddedGenerationMeta } from '../../api/client'

const ja = messagesFor('ja')

function meta(overrides: Partial<EmbeddedGenerationMeta>): EmbeddedGenerationMeta {
  return {
    tool: 'a1111',
    software: null,
    prompt: null,
    negative_prompt: null,
    model: null,
    seed: null,
    params: {},
    raw: {},
    truncated: false,
    verified: false,
    ...overrides,
  }
}

describe('PROMPT_COLLAPSE_LENGTH', () => {
  it('OriginRecipeSection と同じ 80 文字', () => {
    expect(PROMPT_COLLAPSE_LENGTH).toBe(80)
  })
})

describe('describeTool', () => {
  it('software が無ければツール名の訳文だけ', () => {
    expect(describeTool(meta({ tool: 'comfyui' }), ja)).toBe('ComfyUI')
  })

  it('software がツール名と同じ文字列なら添えない', () => {
    expect(describeTool(meta({ tool: 'comfyui', software: 'ComfyUI' }), ja)).toBe('ComfyUI')
  })

  it('software が違う文字列なら " · " で添える', () => {
    expect(describeTool(meta({ tool: 'comfyui', software: 'ComfyUI-portable-0.1' }), ja)).toBe(
      'ComfyUI · ComfyUI-portable-0.1',
    )
  })

  it('software が空文字なら添えない', () => {
    expect(describeTool(meta({ tool: 'a1111', software: '' }), ja)).toBe(
      ja.lineage.embeddedMeta.tools.a1111,
    )
  })

  it('全ツールが辞書に存在する', () => {
    const tools: EmbeddedGenerationMeta['tool'][] = [
      'a1111',
      'comfyui',
      'novelai',
      'invokeai',
      'swarmui',
      'c2pa',
      'generic',
    ]
    for (const tool of tools) {
      expect(describeTool(meta({ tool }), ja)).toBe(ja.lineage.embeddedMeta.tools[tool])
    }
  })
})

describe('paramEntries', () => {
  it('seed が無ければ params だけ、挿入順のまま文字列化する', () => {
    expect(paramEntries(meta({ params: { Steps: '20', 'CFG scale': 7 } }))).toEqual([
      ['Steps', '20'],
      ['CFG scale', '7'],
    ])
  })

  it('seed があれば先頭に置く', () => {
    expect(paramEntries(meta({ seed: 12345, params: { Steps: '20' } }))).toEqual([
      ['seed', '12345'],
      ['Steps', '20'],
    ])
  })

  it('seed が文字列でも String() で扱う', () => {
    expect(paramEntries(meta({ seed: 'abc-123' }))).toEqual([['seed', 'abc-123']])
  })

  it('seed が 0 でも表示する(null/undefined と区別)', () => {
    expect(paramEntries(meta({ seed: 0 }))).toEqual([['seed', '0']])
  })

  it('params が無ければ空配列', () => {
    expect(paramEntries(meta({}))).toEqual([])
  })

  it('真偽値も文字列化する', () => {
    expect(paramEntries(meta({ params: { restore_faces: true } }))).toEqual([['restore_faces', 'true']])
  })
})

describe('rawEntries', () => {
  it('raw をそのままエントリ配列にする', () => {
    expect(rawEntries(meta({ raw: { parameters: '元のテキスト' } }))).toEqual([['parameters', '元のテキスト']])
  })

  it('raw が無ければ空配列', () => {
    expect(rawEntries(meta({}))).toEqual([])
  })
})

describe('isC2paUnknown', () => {
  it('c2pa で software も params も無ければ true', () => {
    expect(isC2paUnknown(meta({ tool: 'c2pa' }))).toBe(true)
  })

  it('c2pa で software があれば false', () => {
    expect(isC2paUnknown(meta({ tool: 'c2pa', software: 'Adobe Photoshop' }))).toBe(false)
  })

  it('c2pa で params があれば false', () => {
    expect(isC2paUnknown(meta({ tool: 'c2pa', params: { c2pa: 'openai' } }))).toBe(false)
  })

  it('c2pa 以外なら false', () => {
    expect(isC2paUnknown(meta({ tool: 'generic' }))).toBe(false)
  })
})

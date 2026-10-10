import { describe, expect, it } from 'vitest'
import { buildParamChips, formatTokenCount, formatTokenCountWithCost } from './historyChips'

describe('buildParamChips', () => {
  it('params が無ければ空配列', () => {
    expect(buildParamChips(undefined)).toEqual([])
  })

  it('指定があるものだけをチップにする', () => {
    expect(buildParamChips({ size: '1024x1024', quality: 'low' })).toEqual(['1024x1024', 'low'])
  })

  it('n は1以下なら出さない、2以上なら ×n で出す', () => {
    expect(buildParamChips({ n: 1 })).toEqual([])
    expect(buildParamChips({ n: 4 })).toEqual(['×4'])
  })

  it('SD WebUI のバッチ回数(n_iter)があれば ×(枚数 × バッチ回数)で出す', () => {
    expect(buildParamChips({ n: 2, n_iter: 3 })).toEqual(['×6'])
    expect(buildParamChips({ n_iter: 4 })).toEqual(['×4'])
    expect(buildParamChips({ n: 1, n_iter: 1 })).toEqual([])
  })

  it('output_format も出す', () => {
    expect(buildParamChips({ output_format: 'png' })).toEqual(['png'])
  })

  it('型が違う値は無視する', () => {
    expect(buildParamChips({ size: 123, quality: null, n: '4' })).toEqual([])
  })

  it('4項目すべて揃うと順番どおりに並ぶ', () => {
    expect(buildParamChips({ size: '1024x1024', quality: 'low', n: 2, output_format: 'png' })).toEqual([
      '1024x1024',
      'low',
      '×2',
      'png',
    ])
  })
})

describe('formatTokenCount', () => {
  it('usage が無ければ null', () => {
    expect(formatTokenCount(null)).toBeNull()
    expect(formatTokenCount(undefined)).toBeNull()
  })

  it('total_tokens が無ければ null', () => {
    expect(formatTokenCount({})).toBeNull()
  })

  it('3桁区切りで tok を付ける', () => {
    expect(formatTokenCount({ total_tokens: 1256 })).toBe('1,256 tok')
    expect(formatTokenCount({ total_tokens: 214 })).toBe('214 tok')
  })
})

describe('formatTokenCountWithCost', () => {
  it('usage が無ければ null(cost_usd があっても)', () => {
    expect(formatTokenCountWithCost(null, 0.031)).toBeNull()
    expect(formatTokenCountWithCost(undefined, 0.031)).toBeNull()
  })

  it('cost_usd が null/undefined ならトークン数だけ', () => {
    expect(formatTokenCountWithCost({ total_tokens: 3316 }, null)).toBe('3,316 tok')
    expect(formatTokenCountWithCost({ total_tokens: 3316 }, undefined)).toBe('3,316 tok')
  })

  it('cost_usd があれば tok の右に $ 表記(≈ は付けない)を添える', () => {
    expect(formatTokenCountWithCost({ total_tokens: 3316 }, 0.0308)).toBe('3,316 tok · $0.031')
  })

  it('$0.0005 未満は < $0.001', () => {
    expect(formatTokenCountWithCost({ total_tokens: 10 }, 0.0001)).toBe('10 tok · < $0.001')
  })
})

import { describe, expect, it } from 'vitest'
import { termsDraftFrom, termsInputFrom } from './ipTerms'

describe('termsInputFrom', () => {
  it('用途は1行に1つで、空行と重複と前後の空白を落とす。空の項目は null', () => {
    const draft = { ...termsDraftFrom(null), purposes: ' モック \n\nコンテ\nモック\n', externalAi: 'local_only' as const }
    expect(termsInputFrom(draft)).toEqual({
      contract_number: null,
      valid_from: null,
      valid_until: null,
      purposes: ['モック', 'コンテ'],
      external_ai: 'local_only',
      notes: null,
    })
  })

  it('既存の条件から入力を作ると、元の値に戻る', () => {
    const terms = {
      version: 2,
      created_at: '2026-11-01T00:00:00Z',
      contract_number: 'C-1',
      valid_from: '2026-11-01',
      valid_until: '2027-10-31',
      purposes: ['モック', 'コンテ'],
      external_ai: 'deny' as const,
      notes: '表記',
    }
    const { version: _v, created_at: _c, ...rest } = terms
    expect(termsInputFrom(termsDraftFrom(terms))).toEqual(rest)
  })
})

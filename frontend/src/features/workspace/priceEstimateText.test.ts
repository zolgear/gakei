import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import { describeEstimate, formatUsd } from './priceEstimateText'
import type { PriceEstimateResponse } from '../../api/client'

const base: PriceEstimateResponse = {
  currency: 'USD',
  total_usd: 0.006,
  unavailable_reason: null,
  output_tokens_per_image: 196,
  n: 1,
  input_image_tokens: 0,
  input_images: [],
  text_tokens: 36,
  unit_prices_per_1m: { text_input: 5, image_input: 8, image_output: 30 },
  pricing_source: 'https://developers.openai.com/api/docs/pricing',
  pricing_checked_at: '2026-09-22',
}

function withOverrides(overrides: Partial<PriceEstimateResponse>): PriceEstimateResponse {
  return { ...base, ...overrides }
}

describe('formatUsd', () => {
  it('$0.0005 未満は < $0.001', () => {
    expect(formatUsd(0.0001)).toBe('< $0.001')
    expect(formatUsd(0.0004)).toBe('< $0.001')
  })

  it('$1 未満は小数3桁、≈ は付けない', () => {
    expect(formatUsd(0.006)).toBe('$0.006')
    expect(formatUsd(0.0005)).toBe('$0.001')
  })

  it('$1 以上は小数2桁', () => {
    expect(formatUsd(1)).toBe('$1.00')
    expect(formatUsd(12.345)).toBe('$12.35')
  })
})

describe('describeEstimate', () => {
  it('有り: total_usd があれば ≈ 表記、内訳を title に含める(入力画像なし)', () => {
    const result = describeEstimate(withOverrides({}))
    expect(result.text).toBe('≈ $0.006')
    expect(result.title).toContain('出力 196 tok × 1 枚(公式の計算式)')
    expect(result.title).toContain('テキスト ≈ 36 tok')
    expect(result.title).toContain('単価(1M tok あたり) 出力 $30 / 入力画像 $8 / テキスト $5(2026-09-22 確認)')
    expect(result.title).toContain('途中経過(partial_images)とマスクは含まない。見積もりであって請求額ではない')
    // 入力画像が無い(generate)ときは入力画像の内訳行を出さない
    // (単価一覧の「入力画像 $8」自体は出るので、内訳行だけを見る)。
    expect(result.title.split('\n')).not.toContainEqual(expect.stringMatching(/^入力画像 \d/))
  })

  it('入力画像あり: 枚数・合計・1枚ごとの内訳を出す', () => {
    const result = describeEstimate(
      withOverrides({
        input_image_tokens: 2560,
        input_images: [
          { asset_id: 'a1', width: 1024, height: 1024, tokens: 1024 },
          { asset_id: 'a2', width: 1536, height: 1024, tokens: 1536 },
        ],
      }),
    )
    expect(result.title).toContain('入力画像 2 枚 2,560 tok(1024×1024: 1,024 / 1536×1024: 1,536)')
  })

  it('無し(quality_auto): 「参考なし」で理由を先頭に置く', () => {
    const result = describeEstimate(
      withOverrides({ total_usd: null, unavailable_reason: 'quality_auto', output_tokens_per_image: null }),
    )
    expect(result.text).toBe('参考なし')
    const lines = result.title.split('\n')
    expect(lines[0]).toBe('画質が「自動」のため計算できません(画質を指定すると出ます)')
    // 出力の行は出さないが(単価一覧の「出力 $30」自体は出る)、テキスト(計算できた分)は続けて出す。
    expect(lines).not.toContainEqual(expect.stringMatching(/^出力 \d/))
    expect(result.title).toContain('テキスト ≈ 36 tok')
  })

  it('無し(size_auto)', () => {
    const result = describeEstimate(
      withOverrides({ total_usd: null, unavailable_reason: 'size_auto', output_tokens_per_image: null }),
    )
    expect(result.text).toBe('参考なし')
    expect(result.title.split('\n')[0]).toBe('サイズが「自動」のため計算できません')
  })

  it('無し(size_invalid)', () => {
    const result = describeEstimate(
      withOverrides({ total_usd: null, unavailable_reason: 'size_invalid', output_tokens_per_image: null }),
    )
    expect(result.title.split('\n')[0]).toBe('サイズが条件外です')
  })

  it('無し(unknown_model)', () => {
    const result = describeEstimate(
      withOverrides({
        total_usd: null,
        unavailable_reason: 'unknown_model',
        output_tokens_per_image: null,
        unit_prices_per_1m: null,
      }),
    )
    const lines = result.title.split('\n')
    expect(lines[0]).toBe('このモデルの単価が未登録です')
    // 単価(1M tok あたり)の行自体は出ない(unit_prices_per_1m が null なので)。
    expect(lines).not.toContainEqual(expect.stringMatching(/^単価\(1M tok あたり\)/))
  })

  it('丸め: 大きいトークン数はカンマ区切りで表示する', () => {
    const result = describeEstimate(withOverrides({ output_tokens_per_image: 12345 }))
    expect(result.title).toContain('出力 12,345 tok × 1 枚')
  })

  it('unit_prices_per_1m が無ければ単価の行を出さない', () => {
    const result = describeEstimate(withOverrides({ unit_prices_per_1m: null }))
    expect(result.title).not.toContain('単価')
  })

  it('$0.0005 未満(< $0.001)のときも ≈ を付けない', () => {
    const result = describeEstimate(withOverrides({ total_usd: 0.0001 }))
    expect(result.text).toBe('< $0.001')
  })
})

describe('describeEstimate (en)', () => {
  afterEach(() => {
    setLocale('ja')
  })

  it('translates the short text and title lines to English', () => {
    setLocale('en')
    const result = describeEstimate(withOverrides({}))
    expect(result.title).toContain('Output 196 tok × 1 image (official formula)')
    expect(result.title).toContain('Text ≈ 36 tok')
    expect(result.title).toContain('Unit price (per 1M tok) output $30 / input image $8 / text $5 (checked 2026-09-22)')
    expect(result.title).toContain('Excludes partial images and masks. Estimated cost, not a bill.')
  })

  it('pluralizes "images" for n > 1', () => {
    setLocale('en')
    const result = describeEstimate(withOverrides({ n: 2 }))
    expect(result.title).toContain('Output 196 tok × 2 images (official formula)')
  })

  it('translates the unavailable reason', () => {
    setLocale('en')
    const result = describeEstimate(
      withOverrides({ total_usd: null, unavailable_reason: 'quality_auto', output_tokens_per_image: null }),
    )
    expect(result.text).toBe('No estimate')
    expect(result.title.split('\n')[0]).toBe('Cannot calculate while quality is "auto" (set a quality to see it)')
  })
})

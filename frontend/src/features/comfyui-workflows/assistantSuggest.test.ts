import { describe, expect, it } from 'vitest'
import { ApiError, type ComfySuggestResponse } from '../../api/client'
import { adoptSuggestion, suggestErrorKind, suggestionDiff, totalTokens } from './assistantSuggest'
import { originsAfterChange, initialOrigins } from './bindingCards'
import { EMPTY_BINDINGS_FORM, type BindingsFormState } from './bindingsForm'

const FORM: BindingsFormState = {
  ...EMPTY_BINDINGS_FORM,
  prompt: { node: '6', input: 'text' },
  negativePrompt: { node: '7', input: 'text' },
  seeds: [{ node: '3', input: 'seed' }],
  width: { node: '5', input: 'width' },
  outputs: ['9'],
}

function response(overrides: Partial<ComfySuggestResponse> = {}): ComfySuggestResponse {
  return {
    suggested_operation: 'generate',
    operation_reason: '',
    operation_matches_reading: true,
    suggested_bindings: {
      prompt: { node: '10', input: 'value' },
      negative_prompt: { node: '7', input: 'text' },
      seed: [{ node: '3', input: 'seed' }],
      width: null,
      height: null,
      batch_size: null,
      images: [{ node: '20', input: 'image' }],
      mask: null,
      outputs: ['9'],
      final_prompt: '12',
    },
    items: [
      { field: 'prompt', reason: 'Primitive の文', matches_reading: false, warnings: [] },
      { field: 'negative_prompt', reason: '', matches_reading: true, warnings: [] },
      { field: 'seed', reason: '', matches_reading: true, warnings: [] },
      { field: 'width', reason: '計算で決まる', matches_reading: true, warnings: ['w'] },
      { field: 'images', reason: '', matches_reading: true, warnings: [] },
      { field: 'outputs', reason: '', matches_reading: true, warnings: [] },
      { field: 'final_prompt', reason: '表示のノード', matches_reading: false, warnings: [] },
    ],
    discarded: [],
    model: 'm',
    usage: { input_tokens: 100, output_tokens: 20, total_tokens: 120 },
    ...overrides,
  }
}

describe('suggestionDiff', () => {
  it('lists only the fields that differ from the current form, in screen order', () => {
    const diff = suggestionDiff(FORM, 'generate', response())
    expect(diff.rows.map((r) => r.field)).toEqual(['prompt', 'width', 'final_prompt'])
    // negative_prompt・seed・outputs が同じ。images は generate では出さない。
    expect(diff.matchingCount).toBe(3)
    expect(diff.operation).toBeNull()
    const width = diff.rows.find((r) => r.field === 'width')!
    expect(width.reason).toBe('計算で決まる')
    expect(width.warnings).toEqual(['w'])
    expect(width.suggested.width).toBeNull()
  })

  it('ignores fields the assistant did not propose (discarded ones)', () => {
    const diff = suggestionDiff(FORM, 'generate', response({ items: [] }))
    expect(diff.rows).toEqual([])
    expect(diff.matchingCount).toBe(0)
  })

  it('compares with the current form, not with the rule-based reading', () => {
    const adopted = adoptSuggestion(FORM, response(), 'prompt')
    const diff = suggestionDiff(adopted, 'generate', response())
    expect(diff.rows.map((r) => r.field)).toEqual(['width', 'final_prompt'])
    expect(diff.matchingCount).toBe(4)
  })

  it('shows the operation when it differs and image slots under edit', () => {
    const diff = suggestionDiff(FORM, 'generate', response({ suggested_operation: 'edit', operation_reason: 'LoadImage' }))
    expect(diff.operation).toEqual({ value: 'edit', reason: 'LoadImage' })
    const edit = suggestionDiff(FORM, 'edit', response())
    expect(edit.rows.map((r) => r.field)).toContain('images')
  })

  it('treats seeds as a set', () => {
    const form = { ...FORM, seeds: [{ node: 'b', input: 'seed' }, { node: 'a', input: 'seed' }] }
    const res = response({
      suggested_bindings: {
        ...response().suggested_bindings,
        seed: [
          { node: 'a', input: 'seed' },
          { node: 'b', input: 'seed' },
        ],
      },
    })
    expect(suggestionDiff(form, 'generate', res).rows.map((r) => r.field)).not.toContain('seed')
  })
})

describe('adoptSuggestion', () => {
  it('replaces only the chosen field and marks it as manual', () => {
    const next = adoptSuggestion(FORM, response(), 'final_prompt')
    expect(next.finalPrompt).toBe('12')
    expect(next.prompt).toEqual(FORM.prompt)
    const origins = originsAfterChange(initialOrigins('reading'), FORM, next, ['final_prompt'])
    expect(origins.final_prompt).toBe('manual')
    expect(origins.prompt).toBe('reading')
  })

  it('can clear a field the assistant left empty', () => {
    expect(adoptSuggestion(FORM, response(), 'width').width).toBeNull()
  })
})

describe('suggestErrorKind', () => {
  it('distinguishes denied, limit and other failures', () => {
    expect(suggestErrorKind(new ApiError(409, 'x', 'hook_denied'))).toBe('denied')
    expect(suggestErrorKind(new ApiError(429, 'x', 'rate_limited'))).toBe('limit')
    expect(suggestErrorKind(new ApiError(502, 'x', 'llm_error'))).toBe('failed')
    expect(suggestErrorKind(new Error('x'))).toBe('failed')
  })
})

describe('totalTokens', () => {
  it('uses the total or sums input and output', () => {
    expect(totalTokens({ total_tokens: 5 })).toBe(5)
    expect(totalTokens({ input_tokens: 3, output_tokens: 4 })).toBe(7)
    expect(totalTokens({})).toBeNull()
    expect(totalTokens(undefined)).toBeNull()
  })
})

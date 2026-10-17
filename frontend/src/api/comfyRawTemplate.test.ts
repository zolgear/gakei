import { describe, expect, it } from 'vitest'
import { jsonBodyWithRawTemplate } from './client'

describe('jsonBodyWithRawTemplate', () => {
  it('ファイルの JSON の文字列をそのまま template に入れ、1.0 を保つ', () => {
    const text = '{"3": {"class_type": "KSampler", "inputs": {"denoise": 1.0}}}'
    const body = jsonBodyWithRawTemplate({ name: 'a', operation: 'generate' }, text)
    expect(body).toContain('"denoise": 1.0')
    expect(JSON.parse(body)).toEqual({
      template: { '3': { class_type: 'KSampler', inputs: { denoise: 1 } } },
      name: 'a',
      operation: 'generate',
    })
  })

  it('ほかの項目が無くても正しい JSON にする', () => {
    expect(JSON.parse(jsonBodyWithRawTemplate({}, '{"1": {}}'))).toEqual({ template: { '1': {} } })
  })
})

import { describe, expect, it } from 'vitest'
import type { AnnotationSettingsResponse } from '../../api/client'
import {
  diffAnnotationForm,
  downloadPercent,
  formFromSettings,
  formatMemoryGb,
  hasChanges,
  isAnyOnnxDownloading,
  isValidAnnotationHourlyLimit,
  isValidOnnxThreshold,
  validateAnnotationForm,
} from './annotationSettings'

function settings(overrides: Partial<AnnotationSettingsResponse> = {}): AnnotationSettingsResponse {
  return {
    auto_on_ingest: false,
    llm_enabled: false,
    llm_model: 'gpt-5.6-luna',
    vlm_enabled: false,
    vlm_model: 'gpt-5.6-luna',
    base_url: null,
    api_style: 'responses',
    language: 'ja',
    tag_language: 'localized',
    hourly_limit: 100,
    onnx_enabled: false,
    onnx_model: 'wd-vit-tagger-v3',
    onnx_threshold: 0.35,
    api_key_set: false,
    onnx_models: [],
    pending_count: 0,
    queued_count: 0,
    calls_last_hour: 0,
    usable_engines: [],
    ...overrides,
  }
}

describe('diffAnnotationForm', () => {
  it('変わっていなければ空', () => {
    const s = settings()
    const body = diffAnnotationForm(formFromSettings(s), s)
    expect(body).toEqual({})
    expect(hasChanges(body)).toBe(false)
  })

  it('変わった項目だけを載せ、数値は数に直す', () => {
    const s = settings()
    const form = {
      ...formFromSettings(s),
      llm_model: ' gpt-x ',
      hourly_limit: '50',
      onnx_threshold: '0.5',
      language: 'en' as const,
      tag_language: 'native' as const,
    }
    expect(diffAnnotationForm(form, s)).toEqual({
      llm_model: 'gpt-x',
      hourly_limit: 50,
      onnx_threshold: 0.5,
      language: 'en',
      tag_language: 'native',
    })
  })

  it('Base URL を空にすると null(OpenAI の設定を流用)を送る', () => {
    const s = settings({ base_url: 'http://localhost:11434/v1' })
    expect(diffAnnotationForm({ ...formFromSettings(s), base_url: '  ' }, s)).toEqual({ base_url: null })
  })

  it('Base URL が未設定のまま空なら送らない', () => {
    const s = settings()
    expect(diffAnnotationForm({ ...formFromSettings(s), base_url: '' }, s)).toEqual({})
  })

  it('同じ値を別の書き方で入れても差分にしない(0.35 と .35)', () => {
    const s = settings()
    expect(diffAnnotationForm({ ...formFromSettings(s), onnx_threshold: '.35', hourly_limit: '0100' }, s)).toEqual({})
  })
})

describe('validateAnnotationForm', () => {
  it('範囲外や空を指摘する', () => {
    const s = settings()
    const errors = validateAnnotationForm({
      ...formFromSettings(s),
      llm_model: '  ',
      hourly_limit: '0',
      onnx_threshold: '1',
    })
    expect(errors).toEqual({ llm_model: true, hourly_limit: true, onnx_threshold: true })
  })

  it('既定値はすべて通る', () => {
    expect(validateAnnotationForm(formFromSettings(settings()))).toEqual({})
  })

  it('上限は 1〜10000 の整数、しきい値は 0.01〜0.99', () => {
    expect(isValidAnnotationHourlyLimit('1')).toBe(true)
    expect(isValidAnnotationHourlyLimit('10000')).toBe(true)
    expect(isValidAnnotationHourlyLimit('10001')).toBe(false)
    expect(isValidAnnotationHourlyLimit('1.5')).toBe(false)
    expect(isValidOnnxThreshold('0.01')).toBe(true)
    expect(isValidOnnxThreshold('0.99')).toBe(true)
    expect(isValidOnnxThreshold('0')).toBe(false)
    expect(isValidOnnxThreshold('abc')).toBe(false)
  })
})

describe('isAnyOnnxDownloading / downloadPercent', () => {
  it('ダウンロード中のモデルがあれば true', () => {
    const model = {
      name: 'wd-vit-tagger-v3' as const,
      size_bytes: 1,
      memory_bytes: 1,
      downloaded: false,
      download_status: 'downloading' as const,
    }
    expect(isAnyOnnxDownloading(settings({ onnx_models: [model] }))).toBe(true)
    expect(isAnyOnnxDownloading(settings({ onnx_models: [{ ...model, download_status: 'idle' }] }))).toBe(false)
    expect(isAnyOnnxDownloading(undefined)).toBe(false)
  })

  it('0〜1 を百分率にする', () => {
    expect(downloadPercent(0.426)).toBe(43)
    expect(downloadPercent(1)).toBe(100)
    expect(downloadPercent(null)).toBeNull()
  })
})

describe('formatMemoryGb', () => {
  it('10 進の GB で小数1桁にする', () => {
    expect(formatMemoryGb(600_000_000)).toBe('0.6')
    expect(formatMemoryGb(1_600_000_000)).toBe('1.6')
  })
})

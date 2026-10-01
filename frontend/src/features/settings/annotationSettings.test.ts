import { describe, expect, it } from 'vitest'
import type { AnnotationConnectionView, AnnotationSettingsResponse } from '../../api/client'
import {
  CONNECTIONS_MAX,
  annotationDraftErrors,
  backfillBlocker,
  canAddConnection,
  changeComfyuiConnection,
  connectionCreateBody,
  connectionDeleteBlocker,
  connectionDisplayName,
  connectionFormFromView,
  diffAnnotationDraft,
  diffAnnotationForm,
  diffConnectionForm,
  downloadPercent,
  draftFromSettings,
  emptyConnectionForm,
  formFromSettings,
  formatMemoryGb,
  hasChanges,
  isAnyOnnxDownloading,
  isUsageCellChanged,
  isValidAnnotationHourlyLimit,
  isValidConnectionBaseUrl,
  isValidConnectionName,
  isValidOnnxThreshold,
  validateAnnotationForm,
  validateConnectionForm,
  type AnnotationDraft,
  type AnnotationForm,
} from './annotationSettings'

function connection(overrides: Partial<AnnotationConnectionView> = {}): AnnotationConnectionView {
  return {
    id: 'openai',
    name: 'OpenAI の設定',
    builtin: true,
    base_url: null,
    api_style: 'responses',
    api_key_set: true,
    in_use: true,
    calls_last_hour: 0,
    ...overrides,
  }
}

const OLLAMA = connection({
  id: 'c1',
  name: 'Ollama',
  builtin: false,
  base_url: 'http://127.0.0.1:11434/v1',
  api_style: 'chat',
  api_key_set: false,
  in_use: false,
})

function settings(overrides: Partial<AnnotationSettingsResponse> = {}): AnnotationSettingsResponse {
  return {
    auto_on_ingest: false,
    llm_enabled: false,
    vlm_enabled: false,
    language: 'ja',
    tag_language: 'localized',
    hourly_limit: 100,
    onnx_enabled: false,
    onnx_model: 'wd-vit-tagger-v3',
    onnx_threshold: 0.35,
    connections: [connection(), OLLAMA],
    profiles: {
      default: {
        llm: { connection_id: 'openai', model: 'gpt-5.6-luna' },
        vlm: { connection_id: 'openai', model: 'gpt-5.6-luna' },
      },
      comfyui: { llm: null, vlm: null },
    },
    onnx_models: [],
    pending_count: 0,
    queued_count: 0,
    calls_last_hour: 0,
    usable_engines: [],
    ...overrides,
  }
}

function withComfyLlm(form: AnnotationForm, connectionId: string | null, model: string): AnnotationForm {
  return { ...form, comfyui: { ...form.comfyui, llm: { connection_id: connectionId, model } } }
}

describe('formFromSettings', () => {
  it('ComfyUI の画像の null は「既定と同じ」(connection_id が null)になる', () => {
    const form = formFromSettings(settings())
    expect(form.default.llm).toEqual({ connection_id: 'openai', model: 'gpt-5.6-luna' })
    expect(form.comfyui.llm).toEqual({ connection_id: null, model: '' })
    expect(form.hourly_limit).toBe('100')
  })

  it('ComfyUI の画像に組があればそのまま入る', () => {
    const s = settings({
      profiles: {
        default: settings().profiles.default,
        comfyui: { llm: { connection_id: 'c1', model: 'qwen3:8b' }, vlm: null },
      },
    })
    expect(formFromSettings(s).comfyui.llm).toEqual({ connection_id: 'c1', model: 'qwen3:8b' })
  })
})

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
      hourly_limit: '50',
      onnx_threshold: '0.5',
      language: 'en' as const,
      tag_language: 'native' as const,
    }
    expect(diffAnnotationForm(form, s)).toEqual({
      hourly_limit: 50,
      onnx_threshold: 0.5,
      language: 'en',
      tag_language: 'native',
    })
  })

  it('既定のマスは変わった用途だけを、前後の空白を除いて送る', () => {
    const s = settings()
    const base = formFromSettings(s)
    const form: AnnotationForm = { ...base, default: { ...base.default, vlm: { connection_id: 'c1', model: ' qwen2.5vl:7b ' } } }
    expect(diffAnnotationForm(form, s)).toEqual({
      profiles: { default: { vlm: { connection_id: 'c1', model: 'qwen2.5vl:7b' } } },
    })
  })

  it('モデル名の空白だけの違いは差分にしない', () => {
    const s = settings()
    const base = formFromSettings(s)
    const form: AnnotationForm = { ...base, default: { ...base.default, llm: { connection_id: 'openai', model: ' gpt-5.6-luna ' } } }
    expect(diffAnnotationForm(form, s)).toEqual({})
  })

  it('ComfyUI の画像で接続先を選ぶと組を送る', () => {
    const s = settings()
    const form = withComfyLlm(formFromSettings(s), 'c1', 'qwen3:8b')
    expect(diffAnnotationForm(form, s)).toEqual({
      profiles: { comfyui: { llm: { connection_id: 'c1', model: 'qwen3:8b' } } },
    })
  })

  it('ComfyUI の画像を「既定と同じ」に戻すと null を送る', () => {
    const s = settings({
      profiles: {
        default: settings().profiles.default,
        comfyui: { llm: { connection_id: 'c1', model: 'qwen3:8b' }, vlm: null },
      },
    })
    const form = withComfyLlm(formFromSettings(s), null, 'qwen3:8b')
    expect(diffAnnotationForm(form, s)).toEqual({ profiles: { comfyui: { llm: null } } })
  })

  it('「既定と同じ」のままなら、残っているモデル名の下書きは送らない', () => {
    const s = settings()
    const form = withComfyLlm(formFromSettings(s), null, 'something')
    expect(diffAnnotationForm(form, s)).toEqual({})
  })

  it('同じ値を別の書き方で入れても差分にしない(0.35 と .35)', () => {
    const s = settings()
    expect(diffAnnotationForm({ ...formFromSettings(s), onnx_threshold: '.35', hourly_limit: '0100' }, s)).toEqual({})
  })
})

describe('changeComfyuiConnection', () => {
  const defaultCell = { connection_id: 'openai', model: 'gpt-5.6-luna' }

  it('「既定と同じ」から切り替えてモデル名が空なら、既定のモデル名を下書きに入れる', () => {
    expect(changeComfyuiConnection({ connection_id: null, model: '' }, 'c1', defaultCell)).toEqual({
      connection_id: 'c1',
      model: 'gpt-5.6-luna',
    })
  })

  it('入力済みのモデル名は残す', () => {
    expect(changeComfyuiConnection({ connection_id: null, model: 'qwen3:8b' }, 'c1', defaultCell)).toEqual({
      connection_id: 'c1',
      model: 'qwen3:8b',
    })
    expect(changeComfyuiConnection({ connection_id: 'c1', model: '' }, 'openai', defaultCell)).toEqual({
      connection_id: 'openai',
      model: '',
    })
  })

  it('「既定と同じ」を選ぶとモデル名の下書きは残したまま null にする', () => {
    expect(changeComfyuiConnection({ connection_id: 'c1', model: 'qwen3:8b' }, null, defaultCell)).toEqual({
      connection_id: null,
      model: 'qwen3:8b',
    })
  })
})

describe('validateAnnotationForm', () => {
  const connections = [connection(), OLLAMA]

  it('範囲外や空を指摘する', () => {
    const base = formFromSettings(settings())
    const errors = validateAnnotationForm(
      {
        ...base,
        default: { ...base.default, llm: { connection_id: 'openai', model: '  ' } },
        hourly_limit: '0',
        onnx_threshold: '1',
      },
      connections,
    )
    expect(errors).toEqual({ default_llm_model: true, hourly_limit: true, onnx_threshold: true })
  })

  it('既定値はすべて通る', () => {
    expect(validateAnnotationForm(formFromSettings(settings()), connections)).toEqual({})
  })

  it('一覧に無い接続先を指摘する', () => {
    const base = formFromSettings(settings())
    const form = withComfyLlm(
      { ...base, default: { ...base.default, vlm: { connection_id: 'gone', model: 'x' } } },
      'gone',
      'y',
    )
    expect(validateAnnotationForm(form, connections)).toEqual({
      default_vlm_connection: true,
      comfyui_llm_connection: true,
    })
  })

  it('ComfyUI の画像は、接続先を選んだマスだけモデル名を検証する', () => {
    const base = formFromSettings(settings())
    expect(validateAnnotationForm(withComfyLlm(base, null, ''), connections)).toEqual({})
    expect(validateAnnotationForm(withComfyLlm(base, 'c1', ' '), connections)).toEqual({ comfyui_llm_model: true })
    expect(validateAnnotationForm(withComfyLlm(base, 'c1', 'x'.repeat(201)), connections)).toEqual({
      comfyui_llm_model: true,
    })
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

describe('接続先のフォーム', () => {
  it('名前は 1〜100 文字', () => {
    expect(isValidConnectionName('Ollama')).toBe(true)
    expect(isValidConnectionName('   ')).toBe(false)
    expect(isValidConnectionName('x'.repeat(100))).toBe(true)
    expect(isValidConnectionName('x'.repeat(101))).toBe(false)
  })

  it('Base URL は http / https で、ユーザー情報・クエリー・フラグメントを含まない', () => {
    expect(isValidConnectionBaseUrl('http://127.0.0.1:11434/v1')).toBe(true)
    expect(isValidConnectionBaseUrl(' https://example.com/v1/ ')).toBe(true)
    expect(isValidConnectionBaseUrl('')).toBe(false)
    expect(isValidConnectionBaseUrl('ftp://example.com')).toBe(false)
    expect(isValidConnectionBaseUrl('example.com/v1')).toBe(false)
    expect(isValidConnectionBaseUrl('https://user:pass@example.com')).toBe(false)
    expect(isValidConnectionBaseUrl('https://example.com/v1?x=1')).toBe(false)
    expect(isValidConnectionBaseUrl('https://example.com/v1?')).toBe(false)
    expect(isValidConnectionBaseUrl('https://example.com/v1#a')).toBe(false)
  })

  it('validateConnectionForm は空の欄を指摘する', () => {
    expect(validateConnectionForm(emptyConnectionForm())).toEqual({ name: true, base_url: true })
    expect(validateConnectionForm({ ...emptyConnectionForm(), name: 'a', base_url: 'http://x' })).toEqual({})
  })

  it('追加の本文は前後の空白を除き、空のキーは送らない', () => {
    const form = { name: ' Ollama ', base_url: ' http://127.0.0.1:11434/v1 ', api_style: 'chat' as const, api_key: '  ' }
    expect(connectionCreateBody(form)).toEqual({
      name: 'Ollama',
      base_url: 'http://127.0.0.1:11434/v1',
      api_style: 'chat',
    })
    expect(connectionCreateBody({ ...form, api_key: ' sk-x ' }).api_key).toBe('sk-x')
  })

  it('編集の差分は変わった項目だけ。末尾の / だけの違いは差分にしない', () => {
    const form = connectionFormFromView(OLLAMA)
    expect(form.api_key).toBe('')
    expect(diffConnectionForm(form, OLLAMA)).toEqual({})
    expect(diffConnectionForm({ ...form, base_url: 'http://127.0.0.1:11434/v1/' }, OLLAMA)).toEqual({})
    expect(diffConnectionForm({ ...form, name: 'Ollama 2', api_style: 'responses' }, OLLAMA)).toEqual({
      name: 'Ollama 2',
      api_style: 'responses',
    })
    expect(diffConnectionForm({ ...form, base_url: 'http://10.0.0.2:11434/v1' }, OLLAMA)).toEqual({
      base_url: 'http://10.0.0.2:11434/v1',
    })
  })
})

describe('接続先の一覧', () => {
  it('追加できるのは組み込みを除いて 50 件まで', () => {
    const custom = Array.from({ length: CONNECTIONS_MAX - 1 }, (_, i) => ({ builtin: false, id: `c${i}` }))
    expect(canAddConnection([{ builtin: true }, ...custom])).toBe(true)
    expect(canAddConnection([{ builtin: true }, ...custom, { builtin: false }])).toBe(false)
  })

  it('削除できない理由(組み込み、使用中)', () => {
    expect(connectionDeleteBlocker(connection())).toBe('builtin')
    expect(connectionDeleteBlocker({ ...OLLAMA, in_use: true })).toBe('in_use')
    expect(connectionDeleteBlocker(OLLAMA)).toBeNull()
  })

  it('組み込みの接続先は画面の言語の名前で出す', () => {
    expect(connectionDisplayName(connection({ name: 'OpenAI の設定' }), 'OpenAI settings')).toBe('OpenAI settings')
    expect(connectionDisplayName(OLLAMA, 'OpenAI settings')).toBe('Ollama')
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

describe('ページの下書き(ADR-0031)', () => {
  it('draftFromSettings はスイッチとモデルの選択も持つ', () => {
    const draft = draftFromSettings(settings({ auto_on_ingest: true, onnx_enabled: true }))
    expect(draft.auto_on_ingest).toBe(true)
    expect(draft.llm_enabled).toBe(false)
    expect(draft.onnx_enabled).toBe(true)
    expect(draft.onnx_model).toBe('wd-vit-tagger-v3')
    expect(draft.hourly_limit).toBe('100')
  })

  it('変わっていなければ空', () => {
    const s = settings()
    expect(diffAnnotationDraft(draftFromSettings(s), s)).toEqual({})
  })

  it('スイッチとモデルの選択も、使い方・数値と一緒に1つの本文にまとめる', () => {
    const s = settings()
    const base = draftFromSettings(s)
    const draft: AnnotationDraft = {
      ...base,
      llm_enabled: true,
      onnx_model: 'wd-swinv2-tagger-v3',
      hourly_limit: '20',
      default: { ...base.default, vlm: { connection_id: 'c1', model: 'qwen2.5vl:7b' } },
    }
    expect(diffAnnotationDraft(draft, s)).toEqual({
      llm_enabled: true,
      onnx_model: 'wd-swinv2-tagger-v3',
      hourly_limit: 20,
      profiles: { default: { vlm: { connection_id: 'c1', model: 'qwen2.5vl:7b' } } },
    })
  })

  it('一度変えて元に戻したスイッチは送らない', () => {
    const s = settings({ vlm_enabled: true })
    const draft = { ...draftFromSettings(s), vlm_enabled: true }
    expect(diffAnnotationDraft(draft, s)).toEqual({})
  })

  it('マスの検証の結果を、下書きのキー(default / comfyui / 数値)にまとめる', () => {
    expect(annotationDraftErrors({})).toEqual({})
    const errors = annotationDraftErrors({
      default_llm_model: true,
      comfyui_vlm_connection: true,
      hourly_limit: true,
    })
    expect(Object.keys(errors).sort()).toEqual(['comfyui', 'default', 'hourly_limit'])
    expect(annotationDraftErrors({ onnx_threshold: true }).onnx_threshold).toBeTruthy()
  })

  it('isUsageCellChanged は書き換えたマスだけを示す', () => {
    const saved = formFromSettings(settings())
    expect(isUsageCellChanged(saved, saved, 'default', 'llm')).toBe(false)
    const edited: AnnotationForm = {
      ...saved,
      default: { ...saved.default, llm: { connection_id: 'openai', model: 'gpt-x' } },
    }
    expect(isUsageCellChanged(edited, saved, 'default', 'llm')).toBe(true)
    expect(isUsageCellChanged(edited, saved, 'default', 'vlm')).toBe(false)
    // 空白だけの違いは変更にしない(送らないため)。
    const spaced: AnnotationForm = {
      ...saved,
      default: { ...saved.default, llm: { connection_id: 'openai', model: ' gpt-5.6-luna ' } },
    }
    expect(isUsageCellChanged(spaced, saved, 'default', 'llm')).toBe(false)
  })

  it('「既定と同じ」のマスは、隠れているモデル名の下書きを比べない', () => {
    const saved = formFromSettings(settings())
    const draft = withComfyLlm(saved, null, 'leftover')
    expect(isUsageCellChanged(draft, saved, 'comfyui', 'llm')).toBe(false)
    expect(isUsageCellChanged(withComfyLlm(saved, 'c1', 'qwen3:8b'), saved, 'comfyui', 'llm')).toBe(true)
  })
})

describe('backfillBlocker', () => {
  const ok = { dirty: false, usableEngineCount: 1, pendingCount: 3, running: false }

  it('押せるときは null', () => {
    expect(backfillBlocker(ok)).toBeNull()
  })

  it('保存していない変更がある間は押せない', () => {
    expect(backfillBlocker({ ...ok, dirty: true })).toBe('unsaved')
  })

  it('使えるエンジンが無い・未実行が無い・実行中も押せない', () => {
    expect(backfillBlocker({ ...ok, usableEngineCount: 0 })).toBe('noEngines')
    expect(backfillBlocker({ ...ok, pendingCount: 0 })).toBe('nothingPending')
    expect(backfillBlocker({ ...ok, running: true })).toBe('running')
  })

  it('未保存の理由を、エンジンが無い理由より先に出す(保存すれば解ける理由を先に)', () => {
    expect(backfillBlocker({ ...ok, dirty: true, usableEngineCount: 0 })).toBe('unsaved')
  })
})

import { describe, expect, it } from 'vitest'
import type { EmbeddingOnnxModelStatus, EmbeddingSettingsResponse } from '../../api/client'
import {
  diffEmbeddingDraft,
  embeddingBackfillBlocker,
  embeddingConnectionChoices,
  embeddingDraftFromSettings,
  isAnyEmbeddingModelDownloading,
  isValidDuplicateThreshold,
  describeStoredModelKey,
  sortLanguages,
  thresholdBaseline,
  thresholdDefault,
  validateEmbeddingDraft,
  type EmbeddingDraft,
} from './embeddingSettings'

function model(overrides: Partial<EmbeddingOnnxModelStatus> = {}): EmbeddingOnnxModelStatus {
  return {
    name: 'clip-vit-b32-u8',
    model_key: 'onnx:clip-vit-b32-u8@d15189d',
    languages: ['en'],
    dim: 512,
    size_bytes: 345_000_000,
    memory_bytes: 600_000_000,
    memory_text_bytes: 300_000_000,
    license: 'MIT',
    heavy: false,
    many_languages: false,
    duplicate_threshold: 0.9,
    duplicate_threshold_default: 0.9,
    downloaded: true,
    download_status: 'idle',
    download_progress: null,
    download_error: null,
    ...overrides,
  }
}

function settings(overrides: Partial<EmbeddingSettingsResponse> = {}): EmbeddingSettingsResponse {
  return {
    enabled: true,
    engine: 'onnx',
    onnx_model: 'clip-vit-b32-u8',
    remote_connection_id: null,
    remote_model: null,
    remote_api_format: 'infinity',
    auto_on_ingest: true,
    duplicate_threshold: 0.9,
    duplicate_threshold_default: 0.9,
    active_model_key: 'onnx:clip-vit-b32-u8@d15189d',
    active_languages: ['en'],
    usable: true,
    index_backend: 'numpy',
    onnx_models: [model()],
    stored: [],
    pending_count: 0,
    queued_count: 0,
    failed_count: 0,
    ...overrides,
  }
}

const CONNECTIONS = [{ id: 'openai' }, { id: 'c1' }]

describe('embeddingDraftFromSettings', () => {
  it('未設定の接続先とモデル名は空文字、しきい値は文字列で持つ', () => {
    expect(embeddingDraftFromSettings(settings())).toEqual({
      enabled: true,
      engine: 'onnx',
      onnx_model: 'clip-vit-b32-u8',
      remote_connection_id: '',
      remote_model: '',
      remote_api_format: 'infinity',
      auto_on_ingest: true,
      duplicate_threshold: '0.9',
    })
  })
})

describe('diffEmbeddingDraft', () => {
  const base = settings()
  const draft = (overrides: Partial<EmbeddingDraft>): EmbeddingDraft => ({
    ...embeddingDraftFromSettings(base),
    ...overrides,
  })

  it('変えていなければ空', () => {
    expect(diffEmbeddingDraft(draft({}), base)).toEqual({})
  })

  it('変えた項目だけを載せ、しきい値は数に直す', () => {
    expect(diffEmbeddingDraft(draft({ enabled: false, duplicate_threshold: '0.93' }), base)).toEqual({
      enabled: false,
      duplicate_threshold: 0.93,
    })
  })

  it('しきい値は、下書きで選んでいるモデルの保存済みの値と比べる(ADR-0044 5章)', () => {
    const eg2 = model({
      name: 'embeddinggemma-2-q8',
      duplicate_threshold: 0.97,
      duplicate_threshold_default: 0.97,
    })
    const saved = settings({ duplicate_threshold: 0.93, onnx_models: [model({ duplicate_threshold: 0.93 }), eg2] })
    const switched = { ...embeddingDraftFromSettings(saved), onnx_model: 'embeddinggemma-2-q8' as const }
    expect(thresholdBaseline(switched, saved)).toBe(0.97)
    expect(thresholdDefault(switched, saved)).toBe(0.97)
    // モデルを選び直してそのモデルの値にしたなら、しきい値は送らない。
    expect(diffEmbeddingDraft({ ...switched, duplicate_threshold: '0.97' }, saved)).toEqual({
      onnx_model: 'embeddinggemma-2-q8',
    })
    expect(diffEmbeddingDraft({ ...switched, duplicate_threshold: '0.95' }, saved)).toEqual({
      onnx_model: 'embeddinggemma-2-q8',
      duplicate_threshold: 0.95,
    })
    // リモートに切り替えたときの既定は 0.9。
    expect(thresholdDefault({ engine: 'remote', onnx_model: 'clip-vit-b32-u8' }, saved)).toBe(0.9)
  })

  it('同じ値を別の書き方で入れても差分にしない', () => {
    expect(diffEmbeddingDraft(draft({ duplicate_threshold: '0.90' }), base)).toEqual({})
  })

  it('リモートの接続先とモデル名は前後の空白を落とし、空なら null で送る', () => {
    expect(
      diffEmbeddingDraft(draft({ engine: 'remote', remote_connection_id: 'c1', remote_model: '  clip  ' }), base),
    ).toEqual({ engine: 'remote', remote_connection_id: 'c1', remote_model: 'clip' })
    const remote = settings({ engine: 'remote', remote_connection_id: 'c1', remote_model: 'clip' })
    expect(
      diffEmbeddingDraft(
        { ...embeddingDraftFromSettings(remote), remote_connection_id: '', remote_model: ' ' },
        remote,
      ),
    ).toEqual({ remote_connection_id: null, remote_model: null })
  })
})

describe('validateEmbeddingDraft', () => {
  const ok = embeddingDraftFromSettings(settings())

  it('ローカルではリモートの欄を見ない', () => {
    expect(validateEmbeddingDraft(ok, CONNECTIONS)).toEqual({})
  })

  it('リモートでは接続先(組み込みを除く一覧にあるもの)とモデル名が要る', () => {
    expect(validateEmbeddingDraft({ ...ok, engine: 'remote' }, CONNECTIONS)).toEqual({
      remote_connection_id: 'connectionRequired',
      remote_model: 'modelRequired',
    })
    expect(
      validateEmbeddingDraft({ ...ok, engine: 'remote', remote_connection_id: 'openai', remote_model: 'm' }, CONNECTIONS),
    ).toEqual({ remote_connection_id: 'connectionUnknown' })
    expect(
      validateEmbeddingDraft({ ...ok, engine: 'remote', remote_connection_id: 'gone', remote_model: 'm' }, CONNECTIONS),
    ).toEqual({ remote_connection_id: 'connectionUnknown' })
    expect(
      validateEmbeddingDraft({ ...ok, engine: 'remote', remote_connection_id: 'c1', remote_model: 'x'.repeat(151) }, CONNECTIONS),
    ).toEqual({ remote_model: 'modelTooLong' })
    expect(
      validateEmbeddingDraft({ ...ok, engine: 'remote', remote_connection_id: 'c1', remote_model: 'clip' }, CONNECTIONS),
    ).toEqual({})
  })

  it('しきい値は 0.5〜1 の数', () => {
    expect(validateEmbeddingDraft({ ...ok, duplicate_threshold: '0.49' }, CONNECTIONS)).toEqual({
      duplicate_threshold: 'threshold',
    })
    expect(validateEmbeddingDraft({ ...ok, duplicate_threshold: 'abc' }, CONNECTIONS)).toEqual({
      duplicate_threshold: 'threshold',
    })
  })
})

describe('isValidDuplicateThreshold', () => {
  it('範囲の端を含む', () => {
    expect(isValidDuplicateThreshold('0.5')).toBe(true)
    expect(isValidDuplicateThreshold('1')).toBe(true)
    expect(isValidDuplicateThreshold('1.01')).toBe(false)
    expect(isValidDuplicateThreshold('')).toBe(false)
  })
})

describe('embeddingConnectionChoices', () => {
  it('組み込みの「OpenAI の設定」は選べない', () => {
    expect(embeddingConnectionChoices(CONNECTIONS)).toEqual([{ id: 'c1' }])
  })
})

describe('embeddingBackfillBlocker', () => {
  const base = { dirty: false, usable: true, pendingCount: 3, running: false }

  it('押せるなら null', () => {
    expect(embeddingBackfillBlocker(base)).toBeNull()
  })

  it('実行中 > 保存していない変更 > 使えない > 対象なし の順で理由を返す', () => {
    expect(embeddingBackfillBlocker({ ...base, running: true, dirty: true })).toBe('running')
    expect(embeddingBackfillBlocker({ ...base, dirty: true, usable: false })).toBe('unsaved')
    expect(embeddingBackfillBlocker({ ...base, usable: false, pendingCount: 0 })).toBe('notUsable')
    expect(embeddingBackfillBlocker({ ...base, pendingCount: 0 })).toBe('nothingPending')
  })
})

describe('isAnyEmbeddingModelDownloading / describeStoredModelKey / sortLanguages', () => {
  it('ダウンロード中のモデルがあれば true', () => {
    expect(isAnyEmbeddingModelDownloading(settings())).toBe(false)
    expect(isAnyEmbeddingModelDownloading(settings({ onnx_models: [model({ download_status: 'downloading' })] }))).toBe(
      true,
    )
    expect(isAnyEmbeddingModelDownloading(undefined)).toBe(false)
  })

  describe('describeStoredModelKey', () => {
    const models = [{ name: 'clip-vit-b32-u8' as const }]
    const connections = [
      { id: 'c1', builtin: false, name: '自宅の Infinity' },
      { id: 'openai', builtin: true, name: 'openai' },
    ]
    const describe_ = (key: string) => describeStoredModelKey(key, models, connections, 'OpenAI の設定')

    it('ローカルのモデルはカタログの名前と短い版', () => {
      expect(describe_('onnx:clip-vit-b32-u8@d15189d7028b43f1d3e65039190477f6af591c2a')).toEqual({
        name: 'clip-vit-b32-u8',
        revision: 'd15189d',
        fake: false,
      })
    })

    it('リモートは「接続先の名前 · モデル名」(モデル名に : を含んでもよい)', () => {
      expect(describe_('remote:c1:jinaai/jina-clip-v2:latest')).toEqual({
        name: '自宅の Infinity · jinaai/jina-clip-v2:latest',
        revision: null,
        fake: false,
      })
      expect(describe_('remote:openai:clip').name).toBe('OpenAI の設定 · clip')
    })

    it('fake: を外して印を付ける', () => {
      expect(describe_('fake:onnx:clip-vit-b32-u8@d15189d7028b43f1d3e65039190477f6af591c2a')).toEqual({
        name: 'clip-vit-b32-u8',
        revision: 'd15189d',
        fake: true,
      })
      expect(describe_('fake:remote:c1:clip')).toEqual({ name: '自宅の Infinity · clip', revision: null, fake: true })
    })

    it('分からない接続先・モデル・形式はキーのまま', () => {
      expect(describe_('remote:gone:clip')).toEqual({ name: 'remote:gone:clip', revision: null, fake: false })
      expect(describe_('onnx:unknown-model@abc')).toEqual({ name: 'onnx:unknown-model@abc', revision: null, fake: false })
      expect(describe_('something-else')).toEqual({ name: 'something-else', revision: null, fake: false })
      expect(describe_('fake:other')).toEqual({ name: 'other', revision: null, fake: true })
    })
  })

  it('日本語を先に並べる', () => {
    expect(sortLanguages(['en', 'ja'])).toEqual(['ja', 'en'])
  })
})

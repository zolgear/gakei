import { describe, expect, it } from 'vitest'
import type { ModWebhookView, PythonModView } from '../../api/client'
import {
  MOD_WEBHOOKS_MAX,
  canAddWebhook,
  diffPythonModForm,
  diffWebhookForm,
  emptyWebhookForm,
  eventKey,
  formatDurationMs,
  formatTestResponse,
  hasManifest,
  isValidInstanceName,
  isValidTimeoutInput,
  isValidWebhookUrl,
  modNameFor,
  moveMod,
  orderedMods,
  providerChoices,
  pythonModEvents,
  pythonModFormFromView,
  pythonModState,
  toggleChoice,
  validatePythonModForm,
  validateWebhookForm,
  waitForVerdictApplies,
  webhookCreateBody,
  webhookFormFromView,
  MOD_EVENTS,
} from './modsSettings'

function webhook(overrides: Partial<ModWebhookView> = {}): ModWebhookView {
  return {
    id: 'w1',
    name: '社内の検査',
    url: 'https://hook.example/gakei',
    enabled: true,
    events: ['ai.before_send'],
    filters: { purposes: [], sources: [], providers: [] },
    send_images: false,
    timeout_seconds: 10,
    on_failure: 'block',
    wait_for_verdict: false,
    order: 1,
    secret_set: true,
    ...overrides,
  }
}

function pythonMod(overrides: Partial<PythonModView> = {}): PythonModView {
  return {
    id: 'local_save',
    name: 'ローカルに保存',
    version: '1.0.0',
    api_version: 1,
    available_events: ['asset.ingested', 'run.finished'],
    error: null,
    loaded: false,
    enabled: false,
    events: null,
    filters: { purposes: [], sources: [], providers: [] },
    timeout_seconds: 10,
    on_failure: 'block',
    wait_for_verdict: false,
    returns_verdicts: true,
    order: null,
    settings: [
      { key: 'folder', type: 'path', label: '保存先', default: '/srv/out', value: '/srv/out' },
      { key: 'limit', type: 'number', label: '上限', default: 5, value: 5 },
      { key: 'json', type: 'bool', label: 'JSON も置く', default: false, value: false },
      { key: 'token', type: 'secret', label: 'トークン', secret_set: true },
    ],
    ...overrides,
  }
}

describe('Webhook のフォーム', () => {
  it('URL は http / https で、ユーザー情報と # を含まない(クエリーは可)', () => {
    expect(isValidWebhookUrl('https://hook.example/gakei')).toBe(true)
    expect(isValidWebhookUrl(' http://10.0.0.1:9000/h?x=1 ')).toBe(true)
    expect(isValidWebhookUrl('')).toBe(false)
    expect(isValidWebhookUrl('ftp://hook.example')).toBe(false)
    expect(isValidWebhookUrl('https://u:p@hook.example')).toBe(false)
    expect(isValidWebhookUrl('https://hook.example/#a')).toBe(false)
    expect(isValidWebhookUrl(`https://hook.example/${'x'.repeat(2000)}`)).toBe(false)
  })

  it('待つ時間は 1〜300 秒', () => {
    expect(isValidTimeoutInput('1')).toBe(true)
    expect(isValidTimeoutInput('2.5')).toBe(true)
    expect(isValidTimeoutInput('300')).toBe(true)
    expect(isValidTimeoutInput('0.5')).toBe(false)
    expect(isValidTimeoutInput('301')).toBe(false)
    expect(isValidTimeoutInput('')).toBe(false)
    expect(isValidTimeoutInput('abc')).toBe(false)
  })

  it('追加では鍵が必須。編集では入れたときだけ確かめる', () => {
    const form = { ...emptyWebhookForm(), name: 'a', url: 'https://h.example' }
    expect(validateWebhookForm(form, true)).toEqual({ secret: true })
    expect(validateWebhookForm({ ...form, secret: 's' }, true)).toEqual({})
    expect(validateWebhookForm(form, false)).toEqual({})
    expect(validateWebhookForm({ ...form, secret: 'x'.repeat(1001) }, false)).toEqual({ secret: true })
    expect(validateWebhookForm(emptyWebhookForm(), true)).toEqual({ name: true, url: true, secret: true })
  })

  it('追加の本文は前後の空白を除き、イベントを決まった順に並べる', () => {
    const body = webhookCreateBody({
      ...emptyWebhookForm(),
      name: ' 検査 ',
      url: ' https://h.example ',
      secret: ' key ',
      events: ['run.finished', 'ai.before_send'],
      timeout: '5',
    })
    expect(body).toMatchObject({
      name: '検査',
      url: 'https://h.example',
      secret: 'key',
      events: ['ai.before_send', 'run.finished'],
      timeout_seconds: 5,
      on_failure: 'block',
      send_images: false,
      enabled: false,
    })
  })

  it('編集の差分は変えた項目だけ。鍵は入れたときだけ送り、値は一覧に出さない', () => {
    const view = webhook()
    const form = webhookFormFromView(view)
    expect(form.secret).toBe('')
    expect(diffWebhookForm(form, view)).toEqual({})
    expect(diffWebhookForm({ ...form, secret: ' new ' }, view)).toEqual({ secret: 'new' })
    expect(
      diffWebhookForm({ ...form, enabled: false, timeout: '20', events: ['ai.before_send', 'run.finished'] }, view),
    ).toEqual({ enabled: false, timeout_seconds: 20, events: ['ai.before_send', 'run.finished'] })
  })

  it('絞り込みは並びが違うだけなら差分にしない', () => {
    const view = webhook({ filters: { purposes: ['image_run', 'assistant'], sources: [], providers: [] } })
    const form = webhookFormFromView(view)
    expect(diffWebhookForm({ ...form, purposes: ['assistant', 'image_run'] }, view)).toEqual({})
    expect(diffWebhookForm({ ...form, providers: ['openai'] }, view)).toEqual({
      filters: { purposes: ['image_run', 'assistant'], sources: [], providers: ['openai'] },
    })
  })

  it('追加できるのは 50 件まで', () => {
    expect(canAddWebhook(new Array(MOD_WEBHOOKS_MAX - 1).fill(0))).toBe(true)
    expect(canAddWebhook(new Array(MOD_WEBHOOKS_MAX).fill(0))).toBe(false)
  })
})

describe('購読の共通の項目', () => {
  it('判定を待つのは asset.ingested か run.finished を選んだときだけ', () => {
    expect(waitForVerdictApplies(['ai.before_send'])).toBe(false)
    expect(waitForVerdictApplies(['asset.ingested'])).toBe(true)
    expect(waitForVerdictApplies(['ai.before_send', 'run.finished'])).toBe(true)
  })

  it('チェックの切り替えは選択肢の順を保つ', () => {
    expect(toggleChoice(['run.finished'], 'asset.ingested', true, MOD_EVENTS)).toEqual(['asset.ingested', 'run.finished'])
    expect(toggleChoice(['asset.ingested', 'run.finished'], 'asset.ingested', false, MOD_EVENTS)).toEqual(['run.finished'])
  })

  it('プロバイダーの選択肢に、保存済みの知らない値も残す', () => {
    expect(providerChoices([])).toContain('comfyui')
    expect(providerChoices(['custom'])).toContain('custom')
    expect(providerChoices(['openai']).filter((p) => p === 'openai')).toHaveLength(1)
  })

  it('イベント名を辞書のキーに直す', () => {
    expect(MOD_EVENTS.map(eventKey)).toEqual(['assetIngested', 'aiBeforeSend', 'runFinished'])
  })
})

describe('Python の Mod', () => {
  it('状態は読み込みエラーを先に出す', () => {
    expect(pythonModState({ error: 'import に失敗', enabled: true })).toBe('error')
    expect(pythonModState({ error: null, enabled: true })).toBe('enabled')
    expect(pythonModState({ error: null, enabled: false })).toBe('disabled')
  })

  it('マニフェストが読めない Mod は名前が無い', () => {
    expect(hasManifest(pythonMod())).toBe(true)
    expect(hasManifest(pythonMod({ name: null }))).toBe(false)
  })

  it('イベントが null ならマニフェストのイベントすべて', () => {
    expect(pythonModEvents(pythonMod())).toEqual(['asset.ingested', 'run.finished'])
    expect(pythonModEvents(pythonMod({ events: ['run.finished'] }))).toEqual(['run.finished'])
  })

  it('フォームは秘密でない項目の値だけを持ち、数値は文字列にする', () => {
    const form = pythonModFormFromView(pythonMod())
    expect(form.values).toEqual({ folder: '/srv/out', limit: '5', json: false })
    expect(form.secrets).toEqual({})
    expect(form.events).toEqual(['asset.ingested', 'run.finished'])
  })

  it('差分: 変えた項目だけ。イベントを全部選んだら null(マニフェストに従う)', () => {
    const view = pythonMod({ events: ['run.finished'] })
    const form = pythonModFormFromView(view)
    expect(diffPythonModForm(form, view)).toEqual({})
    expect(diffPythonModForm({ ...form, events: ['asset.ingested', 'run.finished'] }, view)).toEqual({ events: null })
    expect(diffPythonModForm({ ...form, enabled: true }, view)).toEqual({ enabled: true })
    expect(
      diffPythonModForm({ ...form, values: { ...form.values, limit: '8', json: true, folder: '/srv/other' } }, view),
    ).toEqual({ values: { limit: 8, json: true, folder: '/srv/other' } })
  })

  it('判定を待つ: 判定を返す Mod だけ送る', () => {
    const view = pythonMod({ returns_verdicts: true })
    const form = pythonModFormFromView(view)
    expect(diffPythonModForm({ ...form, wait_for_verdict: true }, view)).toEqual({ wait_for_verdict: true })
    const noVerdicts = pythonMod({ returns_verdicts: false })
    expect(diffPythonModForm({ ...pythonModFormFromView(noVerdicts), wait_for_verdict: true }, noVerdicts)).toEqual({})
  })

  it('数値の空欄は null(既定に戻す)', () => {
    const view = pythonMod()
    const form = pythonModFormFromView(view)
    expect(diffPythonModForm({ ...form, values: { ...form.values, limit: '' } }, view)).toEqual({ values: { limit: null } })
  })

  it('秘密は入れた・消したものだけ送る', () => {
    const view = pythonMod()
    const form = pythonModFormFromView(view)
    expect(diffPythonModForm({ ...form, secrets: { token: ' t ' } }, view)).toEqual({ secrets: { token: 't' } })
    expect(diffPythonModForm({ ...form, clearSecrets: ['token'] }, view)).toEqual({ secrets: { token: null } })
    // 設定されていない秘密は消さない。
    const unset = pythonMod({ settings: [{ key: 'token', type: 'secret', label: 'x', secret_set: false }] })
    expect(diffPythonModForm({ ...pythonModFormFromView(unset), clearSecrets: ['token'] }, unset)).toEqual({})
  })

  it('検証: 待つ時間と数値の項目', () => {
    const view = pythonMod()
    const form = pythonModFormFromView(view)
    expect(validatePythonModForm(form, view)).toEqual({})
    expect(validatePythonModForm({ ...form, timeout: '0' }, view)).toEqual({ timeout: true })
    expect(validatePythonModForm({ ...form, values: { ...form.values, limit: 'x' } }, view)).toEqual({ limit: true })
  })
})

describe('呼ぶ順番', () => {
  const data = {
    webhooks: [webhook({ id: 'w1', name: 'B', order: 2 }), webhook({ id: 'w2', name: 'A', order: 1 })],
    python_mods: [
      pythonMod({ id: 'p1', name: 'C', order: null }),
      pythonMod({ id: 'broken', name: null }),
      pythonMod({ id: 'p2', name: 'D', order: 3, enabled: true }),
    ],
  }

  it('順番の順に並べ、順番の無いものは後ろ。マニフェストの読めない Mod は並べない', () => {
    expect(orderedMods(data).map((m) => m.id)).toEqual(['w2', 'w1', 'p2', 'p1'])
  })

  it('上下に動かした並びを返す。端では null', () => {
    const items = orderedMods(data)
    expect(moveMod(items, 1, -1)).toEqual([
      { kind: 'webhook', id: 'w1' },
      { kind: 'webhook', id: 'w2' },
      { kind: 'python', id: 'p2' },
      { kind: 'python', id: 'p1' },
    ])
    expect(moveMod(items, 0, -1)).toBeNull()
    expect(moveMod(items, items.length - 1, 1)).toBeNull()
  })

  it('配達の一覧の名前は一覧から引き、無ければ id', () => {
    expect(modNameFor(data, { mod_kind: 'webhook', mod_id: 'w2' })).toBe('A')
    expect(modNameFor(data, { mod_kind: 'python', mod_id: 'p2' })).toBe('D')
    expect(modNameFor(data, { mod_kind: 'webhook', mod_id: 'gone' })).toBe('gone')
    expect(modNameFor(undefined, { mod_kind: 'python', mod_id: 'p1' })).toBe('p1')
  })
})

describe('表示', () => {
  it('所要時間', () => {
    expect(formatDurationMs(42)).toBe('42 ms')
    expect(formatDurationMs(1530)).toBe('1.5 s')
  })

  it('テスト送信の応答は JSON を字下げし、無ければ null', () => {
    expect(formatTestResponse(null)).toBeNull()
    expect(formatTestResponse({ decision: 'allow' })).toBe('{\n  "decision": "allow"\n}')
    expect(formatTestResponse('text')).toBe('text')
  })

  it('イベントに載せる名前は 100 文字まで(空は名前なし)', () => {
    expect(isValidInstanceName('')).toBe(true)
    expect(isValidInstanceName('x'.repeat(100))).toBe(true)
    expect(isValidInstanceName('x'.repeat(101))).toBe(false)
  })
})

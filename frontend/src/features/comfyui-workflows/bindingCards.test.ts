import { describe, expect, it } from 'vitest'
import type { ComfyBindingReading, ComfyNodeInfo, ComfyParamTierInfo } from '../../api/client'
import {
  applyAnswer,
  buildValidateRequest,
  compareWithReading,
  effectiveExposedRows,
  enableRecommendedRows,
  generalWarningMessages,
  initialOrigins,
  initiallyExpanded,
  isJustSaved,
  issueTarget,
  issuesForField,
  nodePickerOptions,
  originsAfterChange,
  originsAfterReplace,
  pendingQuestions,
  pickerOptions,
  readingsByField,
  rowBadge,
  rowLevel,
  savedComparison,
  shortNodeId,
  summarizeRows,
  tierExposedRows,
  valuePreview,
  visibleFields,
  withReadingValue,
} from './bindingCards'
import { EMPTY_BINDINGS_FORM, type BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'

const NODES: ComfyNodeInfo[] = [
  {
    id: '459:480',
    class_type: 'PrimitiveStringMultiline',
    title: 'PrimitiveStringMultiline',
    state: 'live',
    inputs: [{ name: 'value', value: 'a cat on a sofa', linked: false }],
  },
  {
    id: '459:481',
    class_type: 'PrimitiveStringMultiline',
    title: 'Style',
    state: 'live',
    inputs: [{ name: 'value', value: 'watercolor style', linked: false }],
  },
  {
    id: '3',
    class_type: 'KSampler',
    title: null,
    state: 'live',
    inputs: [
      { name: 'seed', value: 1, linked: false },
      { name: 'steps', value: 20, linked: false },
      { name: 'positive', value: ['6', 0], linked: true },
    ],
  },
  {
    id: '5',
    class_type: 'EmptyLatentImage',
    title: null,
    state: 'live',
    inputs: [
      { name: 'width', value: 512, linked: false },
      { name: 'height', value: 512, linked: false },
      { name: 'batch_size', value: 1, linked: false },
    ],
  },
  {
    id: '12',
    class_type: 'EmptySD3LatentImage',
    title: null,
    state: 'unused',
    inputs: [
      { name: 'width', value: 1024, linked: false },
      { name: 'height', value: ['20', 0], linked: true },
      { name: 'batch_size', value: 2, linked: false },
    ],
  },
  { id: '9', class_type: 'SaveImage', title: null, state: 'live', inputs: [] },
  { id: '20', class_type: 'PreviewImage', title: null, state: 'display', inputs: [] },
]

function reading(field: ComfyBindingReading['field'], confidence: ComfyBindingReading['confidence'], extra: Partial<ComfyBindingReading> = {}): ComfyBindingReading {
  return { field, confidence, reason: `${field} の理由`, ...extra }
}

const PROMPT_ASK = reading('prompt', 'ask', {
  question: 'プロンプトを入れる場所はどれですか。',
  candidates: [
    { node: '459:480', input: 'value', score: 1, label: 'a', path: [] },
    { node: '459:481', input: 'value', score: 0.5, label: 'b', path: [] },
  ],
})

function form(overrides: Partial<BindingsFormState> = {}): BindingsFormState {
  return { ...EMPTY_BINDINGS_FORM, ...overrides }
}

function row(overrides: Partial<ExposedParamRow>): ExposedParamRow {
  return {
    enabled: false,
    name: 'steps',
    node: '3',
    input: 'steps',
    type: 'int',
    label: 'steps',
    default: 20,
    minimum: null,
    maximum: null,
    step: null,
    choices: null,
    maxLength: null,
    ...overrides,
  }
}

describe('行のバッジと開き方', () => {
  it('読み取りのままなら確信度、手で選んだ・保存済みは出どころを出す', () => {
    const r = reading('prompt', 'ranked')
    expect(rowBadge(r, 'reading')).toBe('ranked')
    expect(rowBadge(r, 'manual')).toBe('manual')
    expect(rowBadge(r, 'saved')).toBe('saved')
    expect(rowBadge(undefined, 'reading')).toBe('absent')
  })

  it('ranked の行だけ最初から開く(ask は問いのカードで答える)', () => {
    expect(initiallyExpanded(reading('prompt', 'ranked'), 'reading')).toBe(true)
    expect(initiallyExpanded(reading('prompt', 'ask'), 'reading')).toBe(false)
    expect(initiallyExpanded(reading('prompt', 'unique'), 'reading')).toBe(false)
    expect(initiallyExpanded(reading('prompt', 'ask'), 'saved')).toBe(false)
  })

  it('generate では画像の枠とマスクの行を出さない', () => {
    expect(visibleFields('generate')).not.toContain('images')
    expect(visibleFields('generate')).not.toContain('mask')
    expect(visibleFields('edit')).toContain('mask')
  })
})

describe('要約', () => {
  it('要確認の件数と、バッジごとの件数を数える', () => {
    const readings = readingsByField([PROMPT_ASK, reading('seed', 'unique'), reading('outputs', 'unique')])
    const summary = summarizeRows(['prompt', 'seed', 'outputs'], readings, initialOrigins('reading'))
    expect(summary).toEqual({ total: 3, decided: 2, toConfirm: 1, counts: { ask: 1, unique: 2 } })
  })
})

describe('問いのカード', () => {
  it('読み取りのままの ask だけを問いにし、幅・高さ・枚数は1つにまとめる', () => {
    const sizeAsk = (field: 'width' | 'height' | 'batch_size') =>
      reading(field, 'ask', { candidates: [{ node: '5', input: null, score: 0, label: '5', path: [] }] })
    const readings = readingsByField([PROMPT_ASK, sizeAsk('width'), sizeAsk('height'), sizeAsk('batch_size')])
    const questions = pendingQuestions(visibleFields('generate'), readings, initialOrigins('reading'))
    expect(questions.map((q) => [q.id, q.fields])).toEqual([
      ['prompt', ['prompt']],
      ['size', ['width', 'height', 'batch_size']],
    ])
    // 手で選んだ項目、保存済みの項目には問わない。
    const origins = { ...initialOrigins('reading'), prompt: 'manual' as const }
    expect(pendingQuestions(['prompt'], readings, origins)).toEqual([])
    expect(pendingQuestions(['prompt'], readings, initialOrigins('saved'))).toEqual([])
  })

  it('答えると候補を差し込み先にする', () => {
    const [question] = pendingQuestions(['prompt'], readingsByField([PROMPT_ASK]), initialOrigins('reading'))
    const next = applyAnswer(form(), question, PROMPT_ASK.candidates![1], NODES)
    expect(next.prompt).toEqual({ node: '459:481', input: 'value' })
  })

  it('大きさの答えは、配線されていない入力だけを差し込み先にする', () => {
    const r = reading('width', 'ask', { candidates: [{ node: '12', input: null, score: 0, label: '12', path: [] }] })
    const question = { id: 'size' as const, fields: ['width', 'height', 'batch_size'] as const, reading: r }
    const next = applyAnswer(form(), { ...question, fields: [...question.fields] }, r.candidates![0], NODES)
    expect(next.width).toEqual({ node: '12', input: 'width' })
    expect(next.height).toBeNull()
    expect(next.batchSize).toEqual({ node: '12', input: 'batch_size' })
  })

  it('画像の枠の問いは今の順を確かめるだけ', () => {
    const images = [
      { node: '10', input: 'image' },
      { node: '11', input: 'image' },
    ]
    const r = reading('images', 'ask')
    expect(applyAnswer(form({ images }), { id: 'images', fields: ['images'], reading: r }, null, NODES).images).toBe(images)
  })
})

describe('出どころの更新', () => {
  it('値が変わった項目と触った項目を「手で選択」にする', () => {
    const before = form({ prompt: { node: '459:480', input: 'value' } })
    const after = form({ prompt: { node: '459:481', input: 'value' } })
    const next = originsAfterChange(initialOrigins('reading'), before, after, ['images'])
    expect(next.prompt).toBe('manual')
    expect(next.images).toBe('manual')
    expect(next.seed).toBe('reading')
  })

  it('ファイルを差し替えたら、新しい読み取りと同じ項目は読み取りに戻す', () => {
    const current = form({ prompt: { node: '459:480', input: 'value' }, outputs: ['9'] })
    const next = originsAfterReplace(
      { ...initialOrigins('reading'), outputs: 'manual' },
      current,
      { prompt: { node: '459:481', input: 'value' }, outputs: ['9'] },
      'manual',
    )
    expect(next.prompt).toBe('manual')
    expect(next.outputs).toBe('reading')
  })
})

describe('保存済みの値と今の読み取り', () => {
  const suggested = { prompt: { node: '459:480', input: 'value' }, seed: [{ node: '3', input: 'seed' }], outputs: ['9'] }

  it('項目ごとに一致・違うを返す(シードと出力は順を問わない)', () => {
    const saved = form({
      prompt: { node: '459:481', input: 'value' },
      seeds: [{ node: '3', input: 'seed' }],
      outputs: ['9'],
    })
    expect(compareWithReading(saved, suggested, 'prompt')).toBe('differ')
    expect(compareWithReading(saved, suggested, 'seed')).toBe('match')
    expect(compareWithReading(saved, suggested, 'outputs')).toBe('match')
    expect(compareWithReading(saved, suggested, 'negative_prompt')).toBe('match')
  })

  it('読み取りが決めきれなかった項目は「違う」と言わない', () => {
    const saved = form({ prompt: { node: '459:481', input: 'value' } })
    expect(savedComparison(saved, {}, PROMPT_ASK, 'prompt')).toBe('undecided')
    expect(savedComparison(saved, {}, reading('prompt', 'unique'), 'prompt')).toBe('differ')
  })

  it('読み取りの値に置き換える', () => {
    const saved = form({ prompt: { node: '459:481', input: 'value' } })
    expect(withReadingValue(saved, suggested, 'prompt').prompt).toEqual({ node: '459:480', input: 'value' })
  })
})

describe('検証の表示', () => {
  const issues = [
    { level: 'error' as const, code: 'wired_input' as const, message: 'm1', field: 'prompt' },
    { level: 'warning' as const, code: 'unused_branch' as const, message: 'm2', field: 'seed[1]' },
    { level: 'error' as const, code: 'duplicate' as const, message: 'm3', field: 'exposed_params.steps' },
    { level: 'error' as const, code: 'edit_requires_image' as const, message: 'm4', field: null },
  ]

  it('サーバーの field を行に振り分ける', () => {
    expect(issueTarget(issues[0])).toEqual({ kind: 'field', field: 'prompt' })
    expect(issueTarget(issues[1])).toEqual({ kind: 'field', field: 'seed' })
    expect(issueTarget(issues[2])).toEqual({ kind: 'exposed', name: 'steps' })
    expect(issueTarget(issues[3])).toEqual({ kind: 'general' })
    expect(issuesForField(issues, 'seed').map((i) => i.message)).toEqual(['m2'])
  })

  it('行の帯はエラーが警告より強い', () => {
    expect(rowLevel([issues[0], issues[1]])).toBe('error')
    expect(rowLevel([issues[1]])).toBe('warning')
    expect(rowLevel([])).toBeNull()
  })

  it('検証の API に送る本文は、未設定の行と名前の誤った公開パラメーターを省く', () => {
    const body = buildValidateRequest({
      template: { '3': { class_type: 'KSampler', inputs: {} } },
      operation: 'generate',
      bindings: form({ seeds: [{ node: '', input: '' }, { node: '3', input: 'seed' }], outputs: ['9'] }),
      exposedRows: [row({ enabled: true }), row({ enabled: true, name: 'Bad Name', input: 'cfg' }), row({ name: 'x', input: 'y' })],
    })
    expect(body.bindings.prompt).toBeNull()
    expect(body.bindings.seed).toEqual([{ node: '3', input: 'seed' }])
    expect(body.bindings.mask).toBeNull()
    expect(body.exposed_params?.map((p) => p.name)).toEqual(['steps'])
  })
})

describe('ノードの名前', () => {
  it('サブグラフの id は先頭と末尾だけにする', () => {
    expect(shortNodeId('12')).toBe('#12')
    expect(shortNodeId('459:474')).toBe('459›#474')
    expect(shortNodeId('1:2:3')).toBe('1›…›#3')
  })

  it('既定値の冒頭は改行を空白にして切る', () => {
    expect(valuePreview('a\nb', 10)).toBe('a b')
    expect(valuePreview('abcdef', 3)).toBe('abc…')
    expect(valuePreview(3, 10)).toBeNull()
  })
})

describe('行内のピッカー', () => {
  it('候補を先に、値の種類が合う入力を後に出し、配線された入力は選べない印を付ける', () => {
    const options = pickerOptions(NODES, 'prompt', PROMPT_ASK.candidates!, '')
    expect(options.candidates.map((o) => o.node)).toEqual(['459:480', '459:481'])
    expect(options.others.some((o) => o.node === '3' && o.input === 'steps')).toBe(false)
    expect(options.others.find((o) => o.input === 'positive')?.wired).toBe(true)
  })

  it('名前の合う入力を候補に入れ、生きているノードを先にする', () => {
    const options = pickerOptions(NODES, 'width', [], '')
    expect(options.candidates.map((o) => o.node)).toEqual(['5', '12'])
    expect(options.candidates[1].state).toBe('unused')
  })

  it('絞り込む', () => {
    const options = pickerOptions(NODES, 'prompt', [], 'style')
    expect([...options.candidates, ...options.others].map((o) => o.node)).toEqual(['459:481'])
  })

  it('出力のノードは保存ノードを候補にする', () => {
    const { candidates, others } = nodePickerOptions(NODES, 'outputs', [], '')
    expect(candidates.map((n) => n.id)).toEqual(['9'])
    expect(others.map((n) => n.id)).toContain('20')
  })
})

describe('公開パラメーターの3段', () => {
  const tiers: ComfyParamTierInfo[] = [
    { name: 'steps', node: '3', input: 'steps', tier: 'recommended' },
    { name: 'cfg', node: '3', input: 'cfg', tier: 'other' },
    { name: 'ckpt', node: '4', input: 'ckpt_name', tier: 'hidden', hidden_reason: 'file_or_model' },
    { name: 'value', node: '459:480', input: 'value', tier: 'other' },
  ]
  const rows = [
    row({}),
    row({ name: 'cfg', input: 'cfg' }),
    row({ name: 'ckpt', node: '4', input: 'ckpt_name' }),
    row({ name: 'value', node: '459:480', input: 'value' }),
  ]

  it('段に分け、差し込み先に使っている入力は公開しない段に回す', () => {
    const bindings = form({ prompt: { node: '459:480', input: 'value' } })
    const tiered = tierExposedRows(rows, tiers, bindings)
    expect(tiered.recommended.map((r) => r.row.name)).toEqual(['steps'])
    expect(tiered.other.map((r) => r.row.name)).toEqual(['cfg'])
    expect(tiered.hidden.map((r) => [r.row.name, r.reason])).toEqual([
      ['ckpt', 'file_or_model'],
      ['value', 'binding'],
    ])
  })

  it('公開している行は段にかかわらず推奨に出す(外せるように)', () => {
    const tiered = tierExposedRows([row({ name: 'cfg', input: 'cfg', enabled: true })], tiers, form())
    expect(tiered.recommended.map((r) => r.row.name)).toEqual(['cfg'])
  })

  it('新規登録では推奨の段を最初から公開する', () => {
    expect(enableRecommendedRows(rows, tiers).map((r) => r.enabled)).toEqual([true, false, false, false])
  })

  it('差し込み先に使っている入力は保存・検証で公開しない', () => {
    const bindings = form({ seeds: [{ node: '3', input: 'steps' }] })
    expect(effectiveExposedRows([row({ enabled: true })], bindings)[0].enabled).toBe(false)
  })
})

describe('保存の直後の印', () => {
  it('justSaved の state だけを見る', () => {
    expect(isJustSaved({ justSaved: true })).toBe(true)
    expect(isJustSaved({ fromComfyUISettings: true })).toBe(false)
    expect(isJustSaved(null)).toBe(false)
  })
})

describe('ファイルの節に出す警告', () => {
  it('読み取りで扱う警告は出さず、それ以外(秘密に見える値など)だけを出す', () => {
    expect(
      generalWarningMessages([
        { kind: 'reading', field: 'prompt', message: 'プロンプトの候補' },
        { kind: 'general', field: null, message: '秘密に見える値' },
        { kind: 'general', field: null, message: '秘密に見える値' },
      ]),
    ).toEqual(['秘密に見える値'])
    expect(generalWarningMessages(undefined)).toEqual([])
  })
})

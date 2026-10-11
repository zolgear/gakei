import { describe, expect, it } from 'vitest'
import type { PromptTemplateResponse } from '../../api/client'
import {
  comboFilledSlotIds,
  draftBlockReasons,
  draftToWriteRequest,
  emptyTemplateDraft,
  filterPromptTemplates,
  indexDraftBlockReasons,
  initialSelectionState,
  lockedSlotIds,
  moveItem,
  newComboDraft,
  newSlotDraft,
  newVariantDraft,
  nextUnusedNumber,
  parsePromptBuild,
  reconcileSelectionState,
  selectionBlockReasons,
  templateToDraft,
  toPromptSelections,
  variantLabel,
  type TemplateDraft,
  type TemplateSelectionState,
} from './promptTemplates'

/**
 * スロット「キャラ」(候補 A / B)、「衣装」(候補 ビキニ / コート)、「背景」(候補なし)と、
 * 「衣装 = ビキニ」を覆う組み合わせ「夏」、「衣装 = コート」を覆う組み合わせ「冬」。
 */
function template(): PromptTemplateResponse {
  return {
    id: 'tpl-1',
    name: 'テスト',
    separator: ', ',
    created_at: '2026-10-11T00:00:00Z',
    updated_at: '2026-10-11T00:00:00Z',
    slots: [
      {
        id: 'slot-char',
        name: 'キャラ',
        default_mode: 'random',
        position: 0,
        variants: [
          { id: 'v-a', name: 'A', text: 'girl a', weight: 1, position: 0 },
          { id: 'v-b', name: 'B', text: 'girl b', weight: 2, position: 1 },
        ],
      },
      {
        id: 'slot-wear',
        name: '衣装',
        default_mode: 'off',
        position: 1,
        variants: [
          { id: 'v-bikini', name: 'ビキニ', text: 'bikini', weight: 1, position: 0 },
          { id: 'v-coat', name: 'コート', text: 'coat', weight: 1, position: 1 },
        ],
      },
      { id: 'slot-bg', name: '背景', default_mode: 'random', position: 2, variants: [] },
    ],
    combos: [
      { id: 'combo-summer', name: '夏', weight: 1, position: 0, variant_ids: ['v-bikini'] },
      { id: 'combo-winter', name: '冬', weight: 1, position: 1, variant_ids: ['v-coat'] },
    ],
  }
}

describe('initialSelectionState', () => {
  it('各スロットを default_mode と先頭の候補で始める', () => {
    const state = initialSelectionState(template())
    expect(state).toEqual({
      comboMode: 'off',
      comboIds: [],
      slots: {
        'slot-char': { mode: 'random', variantId: 'v-a', text: '' },
        'slot-wear': { mode: 'off', variantId: 'v-bikini', text: '' },
        'slot-bg': { mode: 'random', variantId: null, text: '' },
      },
    })
  })
})

describe('reconcileSelectionState', () => {
  it('消えた候補・消えた組み合わせを落とし、残った選択はそのまま持ち越す', () => {
    const before: TemplateSelectionState = {
      comboMode: 'pick',
      comboIds: ['combo-summer', 'combo-gone'],
      slots: {
        'slot-char': { mode: 'fixed', variantId: 'v-gone', text: '' },
        'slot-wear': { mode: 'text', variantId: null, text: 'nude' },
        'slot-gone': { mode: 'off', variantId: null, text: '' },
      },
    }
    const after = reconcileSelectionState(template(), before)
    expect(after.comboIds).toEqual(['combo-summer'])
    expect(after.comboMode).toBe('pick')
    // 消えた候補は先頭に戻る。
    expect(after.slots['slot-char']).toEqual({ mode: 'fixed', variantId: 'v-a', text: '' })
    expect(after.slots['slot-wear']).toEqual({ mode: 'text', variantId: 'v-bikini', text: 'nude' })
    expect(after.slots['slot-gone']).toBeUndefined()
    expect(after.slots['slot-bg']).toEqual({ mode: 'random', variantId: null, text: '' })
  })
})

describe('lockedSlotIds', () => {
  it('何も選んでいなければ何もロックしない', () => {
    expect([...lockedSlotIds(template(), initialSelectionState(template()))]).toEqual([])
  })

  it('「選ぶ」で選んだ組み合わせが指す候補のスロットをロックする', () => {
    const state: TemplateSelectionState = {
      ...initialSelectionState(template()),
      comboMode: 'pick',
      comboIds: ['combo-summer'],
    }
    expect([...lockedSlotIds(template(), state)]).toEqual(['slot-wear'])
  })

  it('「ランダム」なら引かれる可能性のある組み合わせのスロットをすべてロックする', () => {
    const state: TemplateSelectionState = { ...initialSelectionState(template()), comboMode: 'random' }
    // 夏・冬のどちらが引かれても衣装が埋まるので、衣装は指定できない。
    expect([...lockedSlotIds(template(), state)]).toEqual(['slot-wear'])
  })

  it('「使わない」のときは選択を覚えていてもロックしない', () => {
    const state: TemplateSelectionState = {
      ...initialSelectionState(template()),
      comboMode: 'off',
      comboIds: ['combo-summer'],
    }
    expect([...lockedSlotIds(template(), state)]).toEqual([])
  })

  it('テンプレートに無い候補を指す組み合わせは無視する', () => {
    const tpl = template()
    tpl.combos = [{ id: 'combo-x', name: 'X', weight: 1, position: 0, variant_ids: ['v-unknown'] }]
    const state: TemplateSelectionState = {
      ...initialSelectionState(tpl),
      comboMode: 'pick',
      comboIds: ['combo-x'],
    }
    expect([...lockedSlotIds(tpl, state)]).toEqual([])
  })
})

describe('toPromptSelections', () => {
  it('「選ぶ」では選んだ組み合わせだけを並び順のまま並べる', () => {
    const state: TemplateSelectionState = {
      ...initialSelectionState(template()),
      comboMode: 'pick',
      comboIds: ['combo-winter', 'combo-summer'],
    }
    expect(toPromptSelections(template(), state).combos).toEqual([
      { id: 'combo-winter', random: false },
      { id: 'combo-summer', random: false },
    ])
  })

  it('「ランダム」では覚えている選択を送らず、ランダム1件だけを送る', () => {
    const state: TemplateSelectionState = {
      ...initialSelectionState(template()),
      comboMode: 'random',
      comboIds: ['combo-summer'],
    }
    expect(toPromptSelections(template(), state).combos).toEqual([{ id: null, random: true }])
  })

  it('「使わない」では覚えている選択を送らない', () => {
    const state: TemplateSelectionState = {
      ...initialSelectionState(template()),
      comboMode: 'off',
      comboIds: ['combo-summer'],
    }
    expect(toPromptSelections(template(), state).combos).toEqual([])
  })

  it('組み合わせが覆うスロットは slots から外す(サーバーが 422 にするため)', () => {
    const state: TemplateSelectionState = {
      ...initialSelectionState(template()),
      comboMode: 'pick',
      comboIds: ['combo-summer'],
    }
    const slots = toPromptSelections(template(), state).slots ?? {}
    expect(Object.keys(slots).sort()).toEqual(['slot-bg', 'slot-char'])
  })

  it('mode ごとに必要な値だけを入れる', () => {
    const state: TemplateSelectionState = {
      comboMode: 'off',
      comboIds: [],
      slots: {
        'slot-char': { mode: 'fixed', variantId: 'v-b', text: 'ignored' },
        'slot-wear': { mode: 'text', variantId: 'v-bikini', text: 'nude' },
        'slot-bg': { mode: 'off', variantId: null, text: '' },
      },
    }
    expect(toPromptSelections(template(), state).slots).toEqual({
      'slot-char': { mode: 'fixed', variant_id: 'v-b' },
      'slot-wear': { mode: 'text', text: 'nude' },
      'slot-bg': { mode: 'off' },
    })
  })

  it('fixed なのに候補が選べていなければ random に落とす', () => {
    const state: TemplateSelectionState = {
      comboMode: 'off',
      comboIds: [],
      slots: { 'slot-bg': { mode: 'fixed', variantId: null, text: '' } },
    }
    expect(toPromptSelections(template(), state).slots?.['slot-bg']).toEqual({ mode: 'random' })
  })
})

describe('selectionBlockReasons', () => {
  const messages = { textRequired: 'text', noSlots: 'noSlots' }

  it('そのまま生成できる選択では何も言わない', () => {
    expect(selectionBlockReasons(template(), initialSelectionState(template()), messages)).toEqual([])
  })

  it('スロットが無いテンプレートは断る', () => {
    const tpl = template()
    tpl.slots = []
    expect(selectionBlockReasons(tpl, initialSelectionState(tpl), messages)).toEqual(['noSlots'])
  })

  it('その場のテキストが空なら断る', () => {
    const state: TemplateSelectionState = {
      ...initialSelectionState(template()),
      slots: {
        ...initialSelectionState(template()).slots,
        'slot-char': { mode: 'text', variantId: null, text: '   ' },
      },
    }
    expect(selectionBlockReasons(template(), state, messages)).toEqual(['text'])
  })

  it('組み合わせが覆うスロットの空テキストは見ない(そもそも送らない)', () => {
    const state: TemplateSelectionState = {
      ...initialSelectionState(template()),
      comboMode: 'pick',
      comboIds: ['combo-summer'],
      slots: {
        ...initialSelectionState(template()).slots,
        'slot-wear': { mode: 'text', variantId: null, text: '' },
      },
    }
    expect(selectionBlockReasons(template(), state, messages)).toEqual([])
  })
})

describe('parsePromptBuild', () => {
  it('サーバーの内訳を画面で使う形にする', () => {
    const info = parsePromptBuild({
      template_id: 'tpl-1',
      template_name: 'テスト',
      separator: ', ',
      combos: [{ id: 'combo-summer', name: '夏' }],
      slots: [
        { slot_id: 'slot-char', name: 'キャラ', mode: 'fixed', variant_id: 'v-a', variant_name: 'A', text: 'girl a' },
        { slot_id: 'slot-wear', name: '衣装', mode: 'combo', variant_id: 'v-bikini', variant_name: 'ビキニ', text: 'bikini' },
      ],
    })
    expect(info?.templateName).toBe('テスト')
    expect(info?.combos).toEqual([{ id: 'combo-summer', name: '夏' }])
    expect(info?.slots[1]).toEqual({
      slotId: 'slot-wear',
      name: '衣装',
      mode: 'combo',
      variantId: 'v-bikini',
      variantName: 'ビキニ',
      text: 'bikini',
    })
  })

  it('テンプレートを使っていない Run(null・形が違う)では null', () => {
    expect(parsePromptBuild(null)).toBeNull()
    expect(parsePromptBuild(undefined)).toBeNull()
    expect(parsePromptBuild('text')).toBeNull()
    expect(parsePromptBuild({})).toBeNull()
  })

  it('知らない mode は random に寄せる', () => {
    const info = parsePromptBuild({ template_name: 'x', slots: [{ slot_id: 's', name: 'n', mode: 'weird' }] })
    expect(info?.slots[0].mode).toBe('random')
  })
})

describe('comboFilledSlotIds', () => {
  it('mode が combo のスロットだけを返す', () => {
    const ids = comboFilledSlotIds({
      prompt: 'girl a, bikini',
      build: {
        template_name: 'テスト',
        slots: [
          { slot_id: 'slot-char', name: 'キャラ', mode: 'random', text: 'girl a' },
          { slot_id: 'slot-wear', name: '衣装', mode: 'combo', text: 'bikini' },
        ],
      },
    })
    expect([...ids]).toEqual(['slot-wear'])
  })

  it('組み立て結果が無ければ空', () => {
    expect([...comboFilledSlotIds(undefined)]).toEqual([])
  })
})

describe('templateToDraft / draftToWriteRequest', () => {
  it('既存の子は id を付けて残す(付けないと論理削除される)', () => {
    const body = draftToWriteRequest(templateToDraft(template()))
    expect(body.name).toBe('テスト')
    expect(body.separator).toBe(', ')
    expect(body.slots?.map((slot) => slot.id)).toEqual(['slot-char', 'slot-wear', 'slot-bg'])
    expect(body.slots?.[0].variants?.map((v) => v.id)).toEqual(['v-a', 'v-b'])
    expect(body.combos).toEqual([
      { id: 'combo-summer', name: '夏', weight: 1, items: [{ variant_id: 'v-bikini' }] },
      { id: 'combo-winter', name: '冬', weight: 1, items: [{ variant_id: 'v-coat' }] },
    ])
  })

  it('新しい候補は ref を付け、組み合わせからは variant_ref で指す', () => {
    const variant = { ...newVariantDraft(), name: '新', text: 'new text' }
    const slot = { ...newSlotDraft('新スロット'), variants: [variant] }
    const combo = { ...newComboDraft('新組'), variantKeys: [variant.key] }
    const draft: TemplateDraft = { ...emptyTemplateDraft('T'), slots: [slot], combos: [combo] }
    const body = draftToWriteRequest(draft)
    expect(body.slots?.[0].id).toBeUndefined()
    expect(body.slots?.[0].variants?.[0]).toEqual({ ref: variant.key, name: '新', text: 'new text', weight: 1 })
    expect(body.combos?.[0].items).toEqual([{ variant_ref: variant.key }])
  })

  it('テキストが空の候補は送らず、指す先が無くなった組み合わせも送らない', () => {
    const empty = newVariantDraft()
    const slot = { ...newSlotDraft('S'), variants: [empty] }
    const combo = { ...newComboDraft('C'), variantKeys: [empty.key] }
    const body = draftToWriteRequest({ ...emptyTemplateDraft('T'), slots: [slot], combos: [combo] })
    expect(body.slots?.[0].variants).toEqual([])
    expect(body.combos).toEqual([])
  })

  it('名前は前後の空白を落とし、空の候補名は null にする', () => {
    const variant = { ...newVariantDraft(), name: '  ', text: 'x' }
    const slot = { ...newSlotDraft('  S  '), variants: [variant] }
    const body = draftToWriteRequest({ ...emptyTemplateDraft('  T  '), slots: [slot] })
    expect(body.name).toBe('T')
    expect(body.slots?.[0].name).toBe('S')
    expect(body.slots?.[0].variants?.[0].name).toBeNull()
  })

  it('消した候補を指していた参照は落とす', () => {
    const draft = templateToDraft(template())
    // 「ビキニ」を消すと、組み合わせ「夏」は指す先が無くなる。
    draft.slots[1].variants = draft.slots[1].variants.filter((v) => v.id !== 'v-bikini')
    const body = draftToWriteRequest(draft)
    expect(body.combos?.map((combo) => combo.id)).toEqual(['combo-winter'])
  })
})

describe('draftBlockReasons', () => {
  it('整った下書きでは何も言わない', () => {
    expect(draftBlockReasons(templateToDraft(template()))).toEqual([])
  })

  it('テンプレート名の空を言う', () => {
    const draft = { ...templateToDraft(template()), name: '  ' }
    expect(draftBlockReasons(draft)).toEqual([{ code: 'nameRequired' }])
  })

  it('名前が空のスロットをキーで指し、同じ種類も1件ずつ返す', () => {
    const variant = { ...newVariantDraft(), text: 'x' }
    const first = newSlotDraft('')
    const named = { ...newSlotDraft('キャラ'), variants: [variant] }
    const last = newSlotDraft('  ')
    const draft: TemplateDraft = { ...emptyTemplateDraft('T'), slots: [first, named, last] }
    expect(draftBlockReasons(draft)).toEqual([
      { code: 'slotNameRequired', slotKey: first.key },
      { code: 'slotNameRequired', slotKey: last.key },
    ])
  })

  it('テキストが空の候補を、スロットと候補のキーで指す', () => {
    const draft = templateToDraft(template())
    draft.slots[0].variants[1].text = '   '
    expect(draftBlockReasons(draft)).toEqual([
      { code: 'variantTextRequired', slotKey: 'slot-char', variantKey: 'v-b' },
    ])
  })

  it('テキストが空の候補が2つあれば2件返す', () => {
    const draft = templateToDraft(template())
    draft.slots[0].variants[0].text = ''
    draft.slots[1].variants[1].text = '  '
    expect(draftBlockReasons(draft)).toEqual([
      { code: 'variantTextRequired', slotKey: 'slot-char', variantKey: 'v-a' },
      { code: 'variantTextRequired', slotKey: 'slot-wear', variantKey: 'v-coat' },
      // 候補が消えると、それを指していた組み合わせの指す先が無くなる。
      { code: 'comboEmpty', comboKey: 'combo-winter' },
    ])
  })

  it('候補を1つも指していない組み合わせをキーで指す(黙って落とさない)', () => {
    const draft = templateToDraft(template())
    draft.combos[0].variantKeys = []
    expect(draftBlockReasons(draft)).toEqual([{ code: 'comboEmpty', comboKey: 'combo-summer' }])
  })

  it('候補を1つも指していない組み合わせが2つあれば2件返す', () => {
    const draft = templateToDraft(template())
    draft.combos[0].variantKeys = []
    draft.combos[1].variantKeys = []
    expect(draftBlockReasons(draft)).toEqual([
      { code: 'comboEmpty', comboKey: 'combo-summer' },
      { code: 'comboEmpty', comboKey: 'combo-winter' },
    ])
  })

  it('テキストが空の候補しか指していない組み合わせも断る(保存すると指す先が無くなる)', () => {
    const empty = newVariantDraft()
    const slot = { ...newSlotDraft('S'), variants: [empty] }
    const combo = { ...newComboDraft('C'), variantKeys: [empty.key] }
    const draft: TemplateDraft = { ...emptyTemplateDraft('T'), slots: [slot], combos: [combo] }
    expect(draftBlockReasons(draft)).toEqual([
      { code: 'variantTextRequired', slotKey: slot.key, variantKey: empty.key },
      { code: 'comboEmpty', comboKey: combo.key },
    ])
  })

  it('候補を指している組み合わせの名前の空をキーで指す', () => {
    const draft = templateToDraft(template())
    draft.combos[1].name = '  '
    expect(draftBlockReasons(draft)).toEqual([
      { code: 'comboNameRequired', comboKey: 'combo-winter' },
    ])
  })

  it('候補を指していない組み合わせでは名前の空を言わない(そもそも送らない)', () => {
    const draft = templateToDraft(template())
    draft.combos[0].name = ''
    draft.combos[0].variantKeys = []
    expect(draftBlockReasons(draft)).toEqual([{ code: 'comboEmpty', comboKey: 'combo-summer' }])
  })

  it('候補の名前が空でも断らない(名前は任意)', () => {
    const draft = templateToDraft(template())
    draft.slots[0].variants[0].name = ''
    expect(draftBlockReasons(draft)).toEqual([])
  })
})

describe('indexDraftBlockReasons', () => {
  it('何も無ければ全部空', () => {
    const index = indexDraftBlockReasons([])
    expect(index.templateName).toBe(false)
    expect([...index.slotNames]).toEqual([])
    expect([...index.variantTexts]).toEqual([])
    expect([...index.comboNames]).toEqual([])
    expect([...index.comboEmpties]).toEqual([])
  })

  it('種類ごとに、画面がその場で引けるキーの集合にする', () => {
    const draft = templateToDraft(template())
    draft.name = ''
    draft.slots[0].name = ' '
    draft.slots[0].variants[1].text = ''
    draft.combos[1].name = ''
    const index = indexDraftBlockReasons(draftBlockReasons(draft))
    expect(index.templateName).toBe(true)
    expect([...index.slotNames]).toEqual(['slot-char'])
    expect([...index.variantTexts]).toEqual(['v-b'])
    expect([...index.comboNames]).toEqual(['combo-winter'])
    expect([...index.comboEmpties]).toEqual([])
  })

  it('候補を指していない組み合わせはキーで引ける', () => {
    const draft = templateToDraft(template())
    draft.combos[0].variantKeys = []
    const index = indexDraftBlockReasons(draftBlockReasons(draft))
    expect([...index.comboEmpties]).toEqual(['combo-summer'])
    expect([...index.comboNames]).toEqual([])
  })
})

describe('nextUnusedNumber', () => {
  const label = (n: number) => `新しいスロット ${n}`

  it('名前が無ければ 1 から始める', () => {
    expect(nextUnusedNumber([], label)).toBe(1)
  })

  it('使われていない最小の番号を選ぶ(間が空いていれば詰める)', () => {
    expect(nextUnusedNumber(['新しいスロット 1', '新しいスロット 3'], label)).toBe(2)
    expect(nextUnusedNumber(['新しいスロット 1', '新しいスロット 2'], label)).toBe(3)
  })

  it('関係ない名前は番号に影響しない', () => {
    expect(nextUnusedNumber(['キャラ', '衣装'], label)).toBe(1)
  })

  it('前後の空白は無視して重複を見る', () => {
    expect(nextUnusedNumber(['  新しいスロット 1  '], label)).toBe(2)
  })
})

describe('moveItem', () => {
  it('要素を動かした新しい配列を返す', () => {
    expect(moveItem([1, 2, 3], 0, 2)).toEqual([2, 3, 1])
    expect(moveItem([1, 2, 3], 2, 0)).toEqual([3, 1, 2])
  })

  it('範囲外・同じ位置なら元の配列をそのまま返す', () => {
    const items = [1, 2, 3]
    expect(moveItem(items, 1, 1)).toBe(items)
    expect(moveItem(items, 0, 5)).toBe(items)
    expect(moveItem(items, -1, 0)).toBe(items)
  })
})

describe('variantLabel', () => {
  it('名前があれば名前、無ければテキストの先頭', () => {
    expect(variantLabel({ name: 'A', text: 'girl a' })).toBe('A')
    expect(variantLabel({ name: null, text: 'girl a' })).toBe('girl a')
    expect(variantLabel({ name: '  ', text: 'girl a' })).toBe('girl a')
    expect(variantLabel({ name: null, text: 'x'.repeat(50) }, 10)).toBe(`${'x'.repeat(10)}…`)
  })
})

describe('filterPromptTemplates', () => {
  const items = [
    { id: '1', name: '夏のビーチ', separator: ', ', slot_count: 1, variant_count: 1, combo_count: 0, created_at: '', updated_at: '' },
    { id: '2', name: 'Winter', separator: ', ', slot_count: 1, variant_count: 1, combo_count: 0, created_at: '', updated_at: '' },
  ]

  it('空の絞り込みでは元の並びのまま', () => {
    expect(filterPromptTemplates(items, '  ')).toBe(items)
  })

  it('名前の部分一致(大文字小文字を無視)で絞る', () => {
    expect(filterPromptTemplates(items, 'winter').map((i) => i.id)).toEqual(['2'])
    expect(filterPromptTemplates(items, 'ビーチ').map((i) => i.id)).toEqual(['1'])
  })
})

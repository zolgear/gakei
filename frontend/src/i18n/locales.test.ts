/**
 * ja/en の辞書(`locales/{ja,en}.json`)が同じキー構造を持ち、置き場所(`{name}`)の名前の
 * 集合が一致し、英語の値に日本語(ひらがな/カタカナ/漢字)が紛れ込んでいないことを確認する
 * (ADR-0015)。`fmt()` の単体テストも合わせて置く。
 */
import { describe, expect, it } from 'vitest'
import { fmt, messagesFor, type PluralTemplate } from './index'

const JAPANESE_RE = /[぀-ヿ㐀-鿿]/
const PLACEHOLDER_RE = /\{(\w+)\}/g

function isPluralTemplate(value: unknown): value is PluralTemplate {
  return (
    value !== null &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    typeof (value as Record<string, unknown>).one === 'string' &&
    typeof (value as Record<string, unknown>).other === 'string' &&
    Object.keys(value as Record<string, unknown>).length === 2
  )
}

/** 文字列の値(と、`{one,other}` の入れ子)だけをキー・パスの集合として集める。配列はデータなので対象外。 */
function collectKeys(value: unknown, path: string, keys: Set<string>): void {
  if (Array.isArray(value)) return
  if (isPluralTemplate(value)) {
    keys.add(path)
    return
  }
  if (value !== null && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      collectKeys(v, path ? `${path}.${k}` : k, keys)
    }
    return
  }
  keys.add(path)
}

function collectJapaneseStrings(value: unknown, path: string, hits: string[]): void {
  if (Array.isArray(value)) return
  if (isPluralTemplate(value)) {
    if (JAPANESE_RE.test(value.one)) hits.push(`${path}.one`)
    if (JAPANESE_RE.test(value.other)) hits.push(`${path}.other`)
    return
  }
  if (value !== null && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      collectJapaneseStrings(v, path ? `${path}.${k}` : k, hits)
    }
    return
  }
  if (typeof value === 'string' && JAPANESE_RE.test(value)) hits.push(path)
}

function placeholderNames(text: string): Set<string> {
  const names = new Set<string>()
  for (const match of text.matchAll(PLACEHOLDER_RE)) names.add(match[1])
  return names
}

/** 各キー・パスに現れる置き場所の名前の集合(`{one,other}` は両方の和集合)。 */
function collectPlaceholders(value: unknown, path: string, out: Map<string, Set<string>>): void {
  if (Array.isArray(value)) return
  if (isPluralTemplate(value)) {
    const names = new Set([...placeholderNames(value.one), ...placeholderNames(value.other)])
    out.set(path, names)
    return
  }
  if (value !== null && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      collectPlaceholders(v, path ? `${path}.${k}` : k, out)
    }
    return
  }
  if (typeof value === 'string') out.set(path, placeholderNames(value))
}

/**
 * テンプレートリテラルの埋め忘れらしきパターン(`${expr}`。式の中に `.`/`(`/空白などを含む)を
 * 探す。`${output}` のような「$記号 + 単語だけの置き場所」は通貨表記(例: `unitPricesLine`)の
 * 意図した書き方なので対象外にする。
 */
const UNFILLED_TEMPLATE_RE = /\$\{[^}]*[^\w}][^}]*\}/

function collectUnfilledTemplates(value: unknown, path: string, hits: string[]): void {
  if (Array.isArray(value)) return
  if (isPluralTemplate(value)) {
    if (UNFILLED_TEMPLATE_RE.test(value.one)) hits.push(`${path}.one`)
    if (UNFILLED_TEMPLATE_RE.test(value.other)) hits.push(`${path}.other`)
    return
  }
  if (value !== null && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      collectUnfilledTemplates(v, path ? `${path}.${k}` : k, hits)
    }
    return
  }
  if (typeof value === 'string' && UNFILLED_TEMPLATE_RE.test(value)) hits.push(path)
}

describe('i18n locales', () => {
  it('ja と en のキー構造が一致する(one/other も含む)', () => {
    const jaKeys = new Set<string>()
    const enKeys = new Set<string>()
    collectKeys(messagesFor('ja'), '', jaKeys)
    collectKeys(messagesFor('en'), '', enKeys)

    const missingInEn = [...jaKeys].filter((k) => !enKeys.has(k))
    const extraInEn = [...enKeys].filter((k) => !jaKeys.has(k))

    expect(missingInEn, `en に無いキー: ${missingInEn.join(', ')}`).toEqual([])
    expect(extraInEn, `en にだけあるキー: ${extraInEn.join(', ')}`).toEqual([])
  })

  it('置き場所({name})の名前の集合が ja と en で一致する', () => {
    const jaPlaceholders = new Map<string, Set<string>>()
    const enPlaceholders = new Map<string, Set<string>>()
    collectPlaceholders(messagesFor('ja'), '', jaPlaceholders)
    collectPlaceholders(messagesFor('en'), '', enPlaceholders)

    const mismatches: string[] = []
    for (const [path, jaNames] of jaPlaceholders) {
      const enNames = enPlaceholders.get(path)
      if (!enNames) continue // キー構造の一致は別のテストで確かめる
      const jaSorted = [...jaNames].sort()
      const enSorted = [...enNames].sort()
      if (jaSorted.join(',') !== enSorted.join(',')) {
        mismatches.push(`${path}: ja={${jaSorted.join(', ')}} en={${enSorted.join(', ')}}`)
      }
    }
    expect(mismatches, mismatches.join('\n')).toEqual([])
  })

  it('en の辞書に日本語の文字が含まれない', () => {
    const hits: string[] = []
    collectJapaneseStrings(messagesFor('en'), '', hits)
    expect(hits, `日本語が残っているキー: ${hits.join(', ')}`).toEqual([])
  })

  it('埋め忘れたテンプレートリテラル(${ ... })が残っていない', () => {
    const jaHits: string[] = []
    const enHits: string[] = []
    collectUnfilledTemplates(messagesFor('ja'), '', jaHits)
    collectUnfilledTemplates(messagesFor('en'), '', enHits)
    expect(jaHits, `ja: ${jaHits.join(', ')}`).toEqual([])
    expect(enHits, `en: ${enHits.join(', ')}`).toEqual([])
  })
})

describe('fmt', () => {
  it('プレーンな文字列の {name} を置き換える', () => {
    expect(fmt('入力 {count} / {total} 枚', { count: 1, total: 4 })).toBe('入力 1 / 4 枚')
  })

  it('未知の置き場所はそのまま残す', () => {
    expect(fmt('{known} と {unknown}', { known: 'A' })).toBe('A と {unknown}')
  })

  it('params が無ければ置き換えずそのまま返す', () => {
    expect(fmt('プレーン文字列')).toBe('プレーン文字列')
  })

  it('count が1なら one、それ以外は other を選ぶ', () => {
    const template = { one: '{count} item', other: '{count} items' }
    expect(fmt(template, { count: 1 })).toBe('1 item')
    expect(fmt(template, { count: 2 })).toBe('2 items')
    expect(fmt(template, { count: 0 })).toBe('0 items')
  })
})

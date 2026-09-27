/**
 * 画面の文言(ADR-0015)。言語ごとに1つの JSON(`locales/{ja,en}.json`)から読み、
 * コンポーネントでは `useI18n()` の `t`、React の外の関数では `msg()` を使う。
 * どちらも現在の言語の辞書を返す。差し込みは `fmt()` で行う。
 */
import { useSyncExternalStore } from 'react'
import ja from './locales/ja.json'
import enJson from './locales/en.json'
import { getLocale, setLocale, subscribeLocale, type Locale } from './locale'

export { LANGUAGE_SETTING_LABEL, LOCALES, LOCALE_LABELS, getLocale, initLocale, isLocale, setLocale, type Locale } from './locale'

/**
 * 日本語の辞書(`locales/ja.json`)の形が正で、英語(`locales/en.json`)はこれに合わせる。
 * キーが欠けると `tsc -b` が落ちる(`en` を `Messages` 型として宣言しているため)。
 */
export type Messages = typeof ja
const en: Messages = enJson

const DICTIONARIES: Record<Locale, Messages> = { ja, en }

/** 現在の言語の辞書。React の外(フォーマッタや検証関数)から使う。 */
export function msg(): Messages {
  return DICTIONARIES[getLocale()]
}

/** 指定した言語の辞書(テスト用)。 */
export function messagesFor(locale: Locale): Messages {
  return DICTIONARIES[locale]
}

/** `Intl` に渡すロケール名。 */
export function intlLocale(locale: Locale = getLocale()): string {
  return locale === 'ja' ? 'ja-JP' : 'en-US'
}

export function useLocale(): Locale {
  return useSyncExternalStore(subscribeLocale, getLocale, getLocale)
}

export function useI18n(): { locale: Locale; t: Messages; setLocale: (locale: Locale) => void } {
  const locale = useLocale()
  return { locale, t: DICTIONARIES[locale], setLocale }
}

/** 数で文言が変わる形(ADR-0015)。`count` が1なら `one`、それ以外は `other`。 */
export interface PluralTemplate {
  one: string
  other: string
}

/**
 * テンプレート文字列の `{name}` を `params[name]` で置き換える。数で文言が変わるもの
 * (`{ one, other }`)は `params.count` で選んでから置き換える。未知の置き場所はそのまま残す。
 */
export function fmt(template: string | PluralTemplate, params?: Record<string, string | number>): string {
  const text = typeof template === 'string' ? template : template[params?.count === 1 ? 'one' : 'other']
  if (!params) return text
  return text.replace(/\{(\w+)\}/g, (match, name: string) => (name in params ? String(params[name]) : match))
}

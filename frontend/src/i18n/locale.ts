/**
 * 表示言語の状態(ADR-0015)。React の外(純粋関数や API クライアント)からも読めるよう、
 * モジュールに1つだけ持つ。既定は `ja` で、起動時に `initLocale()` がブラウザの言語と
 * 保存済みの選択から決める(テストは `initLocale()` を呼ばないので `ja` のまま)。
 */
export type Locale = 'ja' | 'en'

export const LOCALES: readonly Locale[] = ['ja', 'en']

const STORAGE_KEY = 'gakei.locale'

/**
 * 言語の切り替え欄の見出しと選択肢。どちらの言語で表示していても読めるよう、辞書に置かず
 * 日英を併記した固定の文字列にする(誤って読めない言語に切り替えても戻せるように)。
 */
export const LANGUAGE_SETTING_LABEL = '表示言語 / Language'
export const LOCALE_LABELS: Record<Locale, string> = {
  ja: '日本語 (Japanese)',
  en: 'English (英語)',
}

let current: Locale = 'ja'
const listeners = new Set<() => void>()

export function isLocale(value: unknown): value is Locale {
  return value === 'ja' || value === 'en'
}

/** ブラウザの言語設定から決める。`ja` で始まれば日本語、それ以外は英語。 */
export function detectLocale(languages: readonly string[] | undefined): Locale {
  const first = languages?.find((lang) => lang.trim() !== '')
  if (!first) return 'en'
  return first.toLowerCase().startsWith('ja') ? 'ja' : 'en'
}

function readStored(): Locale | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return isLocale(raw) ? raw : null
  } catch {
    return null
  }
}

/** 起動時に1回呼ぶ。保存済みの選択があればそれを、なければブラウザの言語を使う。 */
export function initLocale(): Locale {
  const languages =
    typeof navigator === 'undefined' ? undefined : navigator.languages?.length ? navigator.languages : [navigator.language]
  current = readStored() ?? detectLocale(languages)
  applyDocumentLang(current)
  return current
}

export function getLocale(): Locale {
  return current
}

export function setLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // 保存できなくても、この画面の間は切り替える。
  }
  if (locale === current) return
  current = locale
  applyDocumentLang(locale)
  for (const listener of listeners) listener()
}

export function subscribeLocale(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function applyDocumentLang(locale: Locale): void {
  if (typeof document !== 'undefined') document.documentElement.lang = locale
}

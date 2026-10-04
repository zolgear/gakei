/**
 * 英語向けのモデルで日本語の文章を検索したときの注記(ADR-0033 2章「英語専用のモデルを選んで
 * いると、日本語で検索してもほとんど当たらない。設定画面と検索画面にそのことを出す」)。
 */
import type { EmbeddingLanguage } from '../../api/client'

/** ひらがな、カタカナ(半角を含む)、漢字。 */
const JAPANESE_RE = /[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]/

export function containsJapanese(text: string): boolean {
  return JAPANESE_RE.test(text)
}

/**
 * 使うモデルが日本語に対応しないと分かっているか。リモートのモデルは対応言語が分からない
 * (`multilingual` も `languages` も null)ので、注記を出さない。
 */
export function isEnglishOnlyModel(model: {
  multilingual?: boolean | null
  languages?: readonly EmbeddingLanguage[] | null
}): boolean {
  if (model.multilingual === false) return true
  if (model.multilingual === true) return false
  return Array.isArray(model.languages) && model.languages.length > 0 && !model.languages.includes('ja')
}

/** 検索画面で「日本語ではほとんど当たらない」旨を出すか。 */
export function shouldShowEnglishOnlyHint(
  query: string,
  model: { multilingual?: boolean | null; languages?: readonly EmbeddingLanguage[] | null } | null | undefined,
): boolean {
  if (!model) return false
  return containsJapanese(query) && isEnglishOnlyModel(model)
}

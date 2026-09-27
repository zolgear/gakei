/**
 * 「作成」フローで、セット名が未入力のときに最初のプロンプトから名前の候補を作る。
 * 純粋関数のみ(副作用なし)にして vitest で単体テストする。
 */

/** 名前候補の最大文字数。 */
export const SET_NAME_SUGGESTION_MAX_LENGTH = 30

/**
 * プロンプト本文から名前の候補を作る。改行・連続する空白は1個の半角スペースにまとめ、
 * 先頭 maxLength 文字までに切り詰める。空文字(空白のみ含む)なら空文字を返す。
 */
export function suggestSetNameFromPrompt(
  promptText: string,
  maxLength: number = SET_NAME_SUGGESTION_MAX_LENGTH,
): string {
  const normalized = promptText.trim().replace(/\s+/g, ' ')
  return normalized.slice(0, maxLength)
}

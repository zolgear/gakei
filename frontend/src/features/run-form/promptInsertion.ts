/**
 * 系列インスペクターの「プロンプトに挿入/置き換え」用の純粋関数。textarea の DOM 操作
 * (フォーカス・カーソル位置の読み取り)は呼び出し側で行い、ここでは文字列計算だけを行う。
 */
import { appendTagsText } from '../prompt-tags/promptTags'

export interface PromptInsertResult {
  text: string
  /** 挿入後にカーソルを置くべき位置(文字数)。 */
  cursor: number
}

/**
 * カーソル位置(`cursorPos`。textarea にフォーカスが無い/不明なときは null)へ挿入する。
 * フォーカスが無ければ末尾に追加する(既存の文字列が改行で終わっていなければ改行を挟む)。
 */
export function insertPromptText(
  current: string,
  insertText: string,
  cursorPos: number | null,
): PromptInsertResult {
  if (cursorPos === null) {
    if (current.length === 0) return { text: insertText, cursor: insertText.length }
    const separator = current.endsWith('\n') ? '' : '\n'
    const text = current + separator + insertText
    return { text, cursor: text.length }
  }
  const clamped = Math.max(0, Math.min(cursorPos, current.length))
  const before = current.slice(0, clamped)
  const after = current.slice(clamped)
  const text = before + insertText + after
  return { text, cursor: before.length + insertText.length }
}

/** 既存のプロンプトが空でなければ、置き換え前に確認が要る(2段階確認)。 */
export function shouldConfirmReplace(current: string): boolean {
  return current.trim().length > 0
}

/**
 * - insert: カーソル位置(無ければ末尾に改行を挟んで)に挿入
 * - replace: 全文を置き換え
 * - append-tags: 末尾に `, ` でつなぐ(画像のタグをプロンプトに使う。ADR-0039 1章)
 */
export type PromptInsertMode = 'insert' | 'replace' | 'append-tags'

/** `useRunFormLogic` が公開する挿入/置き換え関数の型(ResultPane 側から呼ぶ)。 */
export type InsertPromptFn = (text: string, mode: PromptInsertMode, cursorPos: number | null) => void

/**
 * `useRunFormLogic` の `insertPrompt` が実際に呼ぶ計算(DOM に依存しない)。
 * `mode==='replace'` はカーソル位置に関わらず全文を置き換える。
 */
export function computePromptInsertion(
  current: string,
  insertText: string,
  mode: PromptInsertMode,
  cursorPos: number | null,
): PromptInsertResult {
  if (mode === 'replace') {
    return { text: insertText, cursor: insertText.length }
  }
  if (mode === 'append-tags') {
    const text = appendTagsText(current, insertText)
    return { text, cursor: text.length }
  }
  return insertPromptText(current, insertText, cursorPos)
}

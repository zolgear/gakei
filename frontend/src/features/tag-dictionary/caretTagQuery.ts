/**
 * テキストモードのプロンプト欄での、タグの入力アシスト(ADR-0041 3章)の純粋関数。
 * DOM には触れず、文字列とキャレットの位置だけを扱う。
 *
 * キャレットの位置の「入力中の語」は、直前の区切り(カンマ、改行、括弧 `()[]{}<>`、`|`)の後ろから
 * キャレットまで(前の空白は除く)。バックスラッシュでエスケープした文字(`\(`)は区切りにしない。
 * 置き換えると壊れる位置では候補を出さない:
 *
 * - `(tag:1.2)` / `[tag:0.8]` のような重みの括弧の中で、`:` より後ろ(重みの数)。
 * - `<…>` の中。ただし `<lora:名前` を打っている途中は LoRA の候補(`kind: 'lora'`)にする。
 * - `BREAK`、`@` で始まる語(プロンプトセットの呼び出しは `@` のポップオーバーが受け持つ)、
 *   `__` で始まる語(ワイルドカード。作らない)。
 *
 * 置き換えるときは、語(キャレットより後ろの続きも含む)をタグにし、括弧の外(トップレベル)なら
 * 区切りの `, ` を補う。括弧の中(`(long ha|` や `{a|b`)では区切りを足さない。
 */

const DELIMITERS = new Set([',', '\n', '(', ')', '[', ']', '{', '}', '|', '<', '>'])
const OPENERS: Readonly<Record<string, string>> = { '(': ')', '[': ']', '{': '}', '<': '>' }
const CLOSERS = new Set([')', ']', '}', '>'])

export interface TagQueryMatch {
  kind: 'tag' | 'lora'
  /** 置き換える範囲の先頭(LoRA は `<` の位置)。 */
  start: number
  /** 置き換える範囲の終わり(キャレットより後ろの語の続きを含む)。 */
  end: number
  /** 候補を引く文字列(エスケープを外したもの。LoRA は `<lora:` より後ろ)。 */
  query: string
  /** 範囲の外側の括弧の深さ(0 ならトップレベル)。 */
  depth: number
}

interface ScanState {
  /** 開いている括弧(文字と位置)。 */
  stack: Array<{ ch: string; index: number }>
  /** キャレットより前の、最後の区切りの直後の位置。 */
  wordStart: number
}

function scanBefore(text: string, caret: number): ScanState {
  const stack: ScanState['stack'] = []
  let wordStart = 0
  for (let i = 0; i < caret; i++) {
    const ch = text[i]
    if (ch === '\\') {
      i++
      continue
    }
    if (ch in OPENERS) {
      stack.push({ ch, index: i })
    } else if (CLOSERS.has(ch)) {
      // 対応する開き括弧まで閉じる(閉じすぎは無視する)。
      for (let j = stack.length - 1; j >= 0; j--) {
        if (OPENERS[stack[j].ch] === ch) {
          stack.length = j
          break
        }
      }
    }
    if (DELIMITERS.has(ch)) wordStart = i + 1
  }
  return { stack, wordStart: Math.min(wordStart, caret) }
}

/**
 * キャレットより後ろの、語の続きの終わり(次の区切りの手前。後ろの空白は除く)。重みの括弧の中では
 * `:` の手前まで(重みの数は残す)。
 */
function wordEnd(text: string, caret: number, stopAtColon: boolean): number {
  let i = caret
  while (i < text.length) {
    const ch = text[i]
    if (ch === '\\') {
      i += 2
      continue
    }
    if (DELIMITERS.has(ch) || (stopAtColon && ch === ':')) break
    i++
  }
  i = Math.min(i, text.length)
  while (i > caret && /\s/.test(text[i - 1])) i--
  return i
}

/** バックスラッシュのエスケープ(`\(` `\)` など)を外す。 */
function unescape(text: string): string {
  return text.replace(/\\(.)/g, '$1')
}

/** キャレットの位置の入力中の語を探す。候補を出さない位置では null。 */
export function findTagQueryAtCaret(text: string, caret: number): TagQueryMatch | null {
  const pos = Math.max(0, Math.min(caret, text.length))
  const { stack, wordStart } = scanBefore(text, pos)
  const top = stack.length > 0 ? stack[stack.length - 1] : null

  if (top?.ch === '<') {
    // `<lora:名前` の途中だけ LoRA の候補にする(`<lora:名前:重み` の重みや、ほかの `<…>` は出さない)。
    const inner = text.slice(top.index + 1, pos)
    const m = /^lora:([^:<>,\n]*)$/i.exec(inner)
    if (!m) return null
    let end = pos
    const rest = /^[^<>,\n]*>/.exec(text.slice(pos))
    if (rest) end = pos + rest[0].length
    return { kind: 'lora', start: top.index, end, query: m[1].trim(), depth: stack.length - 1 }
  }

  const raw = text.slice(wordStart, pos)
  const leading = raw.length - raw.replace(/^\s+/, '').length
  let start = wordStart + leading
  let word = text.slice(start, pos)
  // 区切りのカンマを省いて `BREAK` の後ろに続けた語(`BREAK long ha`)は、`BREAK` の後ろから。
  const breakPrefix = /^BREAK\s+/.exec(word)
  if (breakPrefix) {
    start += breakPrefix[0].length
    word = word.slice(breakPrefix[0].length)
  }
  if (word.trim() === '') return null
  if (word === 'BREAK') return null
  if (word.startsWith('@') || word.startsWith('__')) return null
  // 重みの括弧の中で `:` より後ろ(`(long hair:1.` の重み)は、置き換えると壊れる。
  const inWeight = top?.ch === '(' || top?.ch === '['
  if (inWeight && /(^|[^\\]):/.test(word)) return null

  const end = Math.max(pos, wordEnd(text, pos, inWeight))
  return { kind: 'tag', start, end, query: unescape(word).trim(), depth: stack.length }
}

/** 区切りの `, `。 */
const SEPARATOR = ', '

/**
 * `match` の範囲を `insert` で置き換えた文字列と、置き換えた後のキャレットの位置。
 * トップレベルなら区切りの `, ` を補う(後ろに既にカンマがあれば、それを `, ` に揃えてその後ろへ)。
 */
export function applyTagCompletion(
  text: string,
  match: Pick<TagQueryMatch, 'start' | 'end' | 'depth'>,
  insert: string,
): { text: string; cursor: number } {
  let end = match.end
  let piece = insert
  if (match.depth === 0) {
    const after = /^[ \t]*,[ \t]*/.exec(text.slice(end))
    if (after) end += after[0].length
    piece = `${insert}${SEPARATOR}`
  }
  const next = text.slice(0, match.start) + piece + text.slice(end)
  return { text: next, cursor: match.start + piece.length }
}

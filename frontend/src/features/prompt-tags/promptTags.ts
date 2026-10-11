/**
 * プロンプトをタグで編集するための純粋関数(ADR-0039)。
 *
 * - プロンプトをトップレベルのカンマで区切ってタグ(チップ)にする。括弧 `()`・角括弧 `[]`・
 *   波括弧 `{}`・山括弧 `<>` の中のカンマでは区切らない。`\(` のようにバックスラッシュで
 *   エスケープした文字は括弧として数えない。`(tag:1.2)`、`<lora:…>`、`{a|b}`、`BREAK` は
 *   それぞれ1つのタグとして、中身を変えずに扱う。
 * - タグモードの編集は、区切りを `, ` に揃える(空の要素と前後の空白を除く)以外は文字列を
 *   書き換えない。
 * - 画像のタグや候補のタグ名(`_` は空白、括弧はそのまま)をプロンプトに入れるときは、括弧を
 *   `\(` `\)` にエスケープする(SD WebUI と ComfyUI では括弧が強調の記法)。OpenAI のフォーム
 *   ではエスケープしない。
 */

const OPENERS = new Set(['(', '[', '{', '<'])
const CLOSERS = new Set([')', ']', '}', '>'])

/** 区切りの `, `。 */
export const TAG_SEPARATOR = ', '

/**
 * トップレベルのカンマで切った生の断片(前後の空白も空の要素も残す)。`depth` は最後の断片を
 * 読み終えた時点の括弧の深さ(入力途中の判定に使う)。閉じ括弧が多すぎても深さは 0 より下げない。
 */
function scanTopLevel(text: string): { pieces: string[]; depth: number } {
  const pieces: string[] = []
  let depth = 0
  let start = 0
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (ch === '\\') {
      // 次の1文字はエスケープ済み(括弧としてもカンマとしても数えない)。
      i++
      continue
    }
    if (OPENERS.has(ch)) {
      depth++
    } else if (CLOSERS.has(ch)) {
      if (depth > 0) depth--
    } else if (ch === ',' && depth === 0) {
      pieces.push(text.slice(start, i))
      start = i + 1
    }
  }
  pieces.push(text.slice(start))
  return { pieces, depth }
}

/** プロンプトをタグに分ける(前後の空白を除き、空の要素は捨てる)。 */
export function splitPromptTags(text: string): string[] {
  return scanTopLevel(text)
    .pieces.map((piece) => piece.trim())
    .filter((piece) => piece.length > 0)
}

/** タグをプロンプトにする(区切りは `, `)。 */
export function joinPromptTags(tags: readonly string[]): string {
  return tags.join(TAG_SEPARATOR)
}

/** `index` 番目のタグを消したプロンプト。 */
export function removePromptTag(text: string, index: number): string {
  const tags = splitPromptTags(text)
  if (index < 0 || index >= tags.length) return text
  return joinPromptTags([...tags.slice(0, index), ...tags.slice(index + 1)])
}

/** タグを後ろに足したプロンプト(空のものは足さない)。 */
export function addPromptTags(text: string, additions: readonly string[]): string {
  const extra = additions.map((tag) => tag.trim()).filter((tag) => tag.length > 0)
  if (extra.length === 0) return text
  return joinPromptTags([...splitPromptTags(text), ...extra])
}

/**
 * タグの入力欄の文字列を、確定したタグ(トップレベルのカンマより前)と、入力中の残りに分ける。
 * 括弧を開いたままのカンマ(`(a, b` を打っている途中)では確定しない。
 */
export function splitTypedTags(buffer: string): { complete: string[]; rest: string } {
  const { pieces } = scanTopLevel(buffer)
  const rest = pieces.pop() ?? ''
  return {
    complete: pieces.map((piece) => piece.trim()).filter((piece) => piece.length > 0),
    rest: rest.replace(/^\s+/, ''),
  }
}

/** 括弧(エスケープされていないもの)を `\(` `\)` にする。 */
export function escapePromptParens(text: string): string {
  let out = ''
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (ch === '\\' && i + 1 < text.length) {
      out += ch + text[i + 1]
      i++
      continue
    }
    out += ch === '(' || ch === ')' ? `\\${ch}` : ch
  }
  return out
}

/** `\(` `\)` を括弧に戻す(候補や画像のタグとの比較用)。 */
export function unescapePromptParens(text: string): string {
  return text.replace(/\\([()])/g, '$1')
}

/** タグ名(`_` は空白にする)をプロンプトに入れる形にする。 */
export function tagNameToPrompt(name: string, escapeParens: boolean): string {
  const spaced = name.replace(/_/g, ' ').trim()
  return escapeParens ? escapePromptParens(spaced) : spaced
}

/** タグ名の並びを、プロンプトに入れるカンマ区切りの文字列にする。 */
export function tagNamesToPrompt(names: readonly string[], escapeParens: boolean): string {
  return joinPromptTags(names.map((name) => tagNameToPrompt(name, escapeParens)).filter((t) => t.length > 0))
}

/** 既に入っているタグとの重複の判定に使う形(エスケープを外し、小文字、空白を1つに)。 */
export function tagCompareKey(tag: string): string {
  return unescapePromptParens(tag).replace(/_/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase()
}

/** 括弧をエスケープしないプロバイダー。OpenAI は文章で書く。`fake` は確認用の OpenAI の代わり。 */
const NO_ESCAPE_PROVIDERS = new Set(['', 'openai', 'fake'])

/**
 * フォームの送り先がプロバイダー `provider` のとき、括弧をエスケープするか。プロバイダーが
 * まだ決まっていない(空)ときは既定の OpenAI と同じ扱い。
 */
export function shouldEscapeParens(provider: string): boolean {
  return !NO_ESCAPE_PROVIDERS.has(provider)
}

/**
 * 今のプロンプトの後ろにタグの文字列を足す(区切りは `, `)。今のプロンプトに既にあるタグ
 * (エスケープと大文字小文字を無視して同じもの)は足さない。今のプロンプトは書き換えない。
 */
export function appendTagsText(current: string, addition: string): string {
  const present = new Set(splitPromptTags(current).map(tagCompareKey))
  const extra = splitPromptTags(addition).filter((tag) => !present.has(tagCompareKey(tag)))
  if (extra.length === 0) return current
  const joined = joinPromptTags(extra)
  const base = current.replace(/\s+$/, '')
  if (base === '') return joined
  return base.endsWith(',') ? `${base} ${joined}` : `${base}${TAG_SEPARATOR}${joined}`
}

export type PromptTagKind = 'break' | 'lora' | 'dynamic' | 'weighted' | 'tag'

/** チップの見た目を分けるための種類(中身は変えない)。 */
export function promptTagKind(tag: string): PromptTagKind {
  if (tag === 'BREAK') return 'break'
  if (tag.startsWith('<')) return 'lora'
  if (tag.startsWith('{')) return 'dynamic'
  if (tag.startsWith('(') || tag.startsWith('[')) return 'weighted'
  return 'tag'
}

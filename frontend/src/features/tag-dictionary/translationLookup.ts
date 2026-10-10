/**
 * プロンプトのチップ(ADR-0039)から、訳を引く名前を取り出す(ADR-0041 4章)。
 * 普通のタグはそのまま(エスケープを外す)、重みの括弧 `(tag:1.2)` `((tag))` `[tag]` は中のタグ名。
 * LoRA・Dynamic Prompts・BREAK、括弧の中にカンマがあるもの(複数のタグ)は引かない(null)。
 */
import { promptTagKind, unescapePromptParens } from '../prompt-tags/promptTags'

export function translationLookupName(tag: string): string | null {
  const kind = promptTagKind(tag)
  if (kind === 'tag') return unescapePromptParens(tag).trim() || null
  if (kind !== 'weighted') return null
  // 外側の括弧を(対になっている分だけ)外し、末尾の `:重み` を外す。
  let inner = tag.trim()
  while (/^[([]/.test(inner) && /[)\]]$/.test(inner) && !/\\[)\]]$/.test(inner)) {
    inner = inner.slice(1, -1).trim()
  }
  inner = inner.replace(/:\s*-?[\d.]+$/, '').trim()
  if (inner === '' || /[,()[\]{}<>|]/.test(unescapePromptParens(inner).replace(/[()]/g, ''))) return null
  return unescapePromptParens(inner).trim() || null
}

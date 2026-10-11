/**
 * LoRA の選択(ADR-0038 8章)の純粋関数。プロンプトへの挿入そのものは `insertPrompt` の
 * `append-tags`(末尾に `, ` でつなぎ、同じタグは足さない)を使う。
 */
import type { SdWebuiLora } from '../../api/client'
import { splitPromptTags, tagCompareKey, tagNameToPrompt } from '../prompt-tags/promptTags'

export const LORA_WEIGHT_MIN = -2
export const LORA_WEIGHT_MAX = 2
export const LORA_WEIGHT_STEP = 0.1
export const LORA_WEIGHT_DEFAULT = 1

/** 重みを範囲に収め、0.1 刻みに丸める。数でなければ既定値。 */
export function normalizeLoraWeight(value: number): number {
  if (!Number.isFinite(value)) return LORA_WEIGHT_DEFAULT
  const clamped = Math.min(LORA_WEIGHT_MAX, Math.max(LORA_WEIGHT_MIN, value))
  const rounded = Math.round(clamped / LORA_WEIGHT_STEP) / 10
  // -0 を 0 にする
  return rounded === 0 ? 0 : rounded
}

/** 重みの表記(`1`、`0.8`、`-0.5`)。 */
export function formatLoraWeight(value: number): string {
  return String(normalizeLoraWeight(value))
}

/** プロンプトに入れる `<lora:name:重み>`。名前は alias ではなく name を使う。 */
export function loraPromptTag(name: string, weight: number): string {
  return `<lora:${name}:${formatLoraWeight(weight)}>`
}

/** 検索。空白で区切った語がすべて name か alias に含まれるもの(大文字小文字を無視)。 */
export function filterLoras(items: readonly SdWebuiLora[], query: string): SdWebuiLora[] {
  const terms = query.toLowerCase().split(/\s+/).filter((term) => term.length > 0)
  if (terms.length === 0) return [...items]
  return items.filter((item) => {
    const haystack = `${item.name}\n${item.alias ?? ''}`.toLowerCase()
    return terms.every((term) => haystack.includes(term))
  })
}

/** トリガーワードの候補を、プロンプトに入れる形にする(タグモードの候補と同じエスケープ)。 */
export function triggerTagToPrompt(tag: string, escapeParens: boolean): string {
  return tagNameToPrompt(tag, escapeParens)
}

/** プロンプトに既にあるタグ(比較用の形)の集合。 */
export function promptTagKeys(prompt: string): Set<string> {
  return new Set(splitPromptTags(prompt).map(tagCompareKey))
}

/** このプロンプトに、この LoRA(重みを問わない)が入っているか。 */
export function promptHasLora(prompt: string, name: string): boolean {
  const prefix = `<lora:${name}:`.toLowerCase()
  return splitPromptTags(prompt).some((tag) => tag.toLowerCase().startsWith(prefix))
}

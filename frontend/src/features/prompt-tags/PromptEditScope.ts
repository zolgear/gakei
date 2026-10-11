/**
 * 生成のフォームの今の(プロバイダー, モデル)を、パラメーターの欄(ネガティブプロンプト)まで
 * 届けるための context(ADR-0039 2章)。タグ編集モードをモデルごとに覚えるのと、括弧を
 * エスケープするかの判断に使う。フォームの外(context が無いところ)では null。
 */
import { createContext, useContext } from 'react'

export interface PromptEditScopeValue {
  provider: string
  model: string
}

export const PromptEditScopeContext = createContext<PromptEditScopeValue | null>(null)

export function usePromptEditScope(): PromptEditScopeValue | null {
  return useContext(PromptEditScopeContext)
}

/** タグ編集モードを出すパラメーター(文章の欄のうち、名前が `prompt` で終わるもの)。 */
export function isPromptLikeParam(name: string): boolean {
  return /(^|_)prompt$/.test(name)
}

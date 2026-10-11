/**
 * (プロバイダー, モデル, 欄)ごとに覚えた「テキスト / タグ」のモード(ADR-0039 2章)。
 * モデルを切り替えたら、そのモデルで覚えたモードに切り替わる。
 */
import { useState } from 'react'
import { loadPromptEditMode, savePromptEditMode, type PromptEditMode } from './promptEditModePrefs'

export function usePromptEditMode(
  provider: string,
  model: string,
  field: string,
): [PromptEditMode, (mode: PromptEditMode) => void] {
  const scope = `${provider}\n${model}\n${field}`
  const [state, setState] = useState(() => ({ scope, mode: loadPromptEditMode(provider, model, field) }))
  // モデル(や欄)が変わったら、そのモデルで覚えたモードを読み直す(描画中の state の更新)。
  let current = state
  if (state.scope !== scope) {
    current = { scope, mode: loadPromptEditMode(provider, model, field) }
    setState(current)
  }

  function setMode(mode: PromptEditMode) {
    savePromptEditMode(provider, model, field, mode)
    setState({ scope, mode })
  }

  return [current.mode, setMode]
}

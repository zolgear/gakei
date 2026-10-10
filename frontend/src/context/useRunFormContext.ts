/** RunFormContext のコンテキストオブジェクトと読み出し用フック。 */
import { createContext, useContext } from 'react'
import type { SdWebuiImportParamsResponse } from '../api/client'
import type { PromptInsertMode } from '../features/run-form/promptInsertion'
import type { RunFormState } from '../features/run-form/types'

export interface RunFormContextValue {
  formState: RunFormState
  setFormState: (state: RunFormState) => void
  /**
   * スタジオの外からプロンプト欄へ入れたいテキストのリクエスト。プロンプトセットの
   * サイドバーの「末尾に追加」(mode='insert')と、ビューア・Run 詳細の「最終プロンプト」の
   * 挿入・置き換え(ADR-0030 3章)が使う。スタジオ(useRunFormLogic)が消費して、ローカルの
   * prompt state に反映する(insert はフォーカスが無い扱いなので末尾に追加)。
   * nonce は同じテキストを連続で選んでも取りこぼさず・二重消費もしないための通し番号。
   * localStorage には保存しない(その場限りのリクエストのため)。
   */
  pendingPromptInsert: { text: string; mode: PromptInsertMode; nonce: number } | null
  /** mode の既定は 'insert'。 */
  requestPromptInsert: (text: string, mode?: PromptInsertMode) => void
  clearPendingPromptInsert: () => void
  /**
   * 画像の生成情報から SD WebUI のフォームへ読み込むリクエスト(ADR-0038 9章)。ビューアの
   * 「SD WebUI のフォームに読み込む」とスタジオの「画像から設定を読み込む」が積み、スタジオ
   * (useRunFormLogic)が capabilities に照らして消費する。プロンプトの置き換えの確認は積む側で
   * 済ませる。nonce は二重消費を防ぐ通し番号。localStorage には保存しない。
   */
  pendingFormLoad: { response: SdWebuiImportParamsResponse; nonce: number } | null
  requestFormLoad: (response: SdWebuiImportParamsResponse) => void
  clearPendingFormLoad: () => void
}

export const RunFormContext = createContext<RunFormContextValue | null>(null)

export function useRunFormContext(): RunFormContextValue {
  const ctx = useContext(RunFormContext)
  if (ctx === null) {
    throw new Error('useRunFormContext must be used inside RunFormProvider')
  }
  return ctx
}

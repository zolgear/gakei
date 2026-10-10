/** RunFormContext のコンテキストオブジェクトと読み出し用フック。 */
import { createContext, useContext } from 'react'
import type { ParameterSetResponse, SdWebuiImportParamsResponse } from '../api/client'
import type { PromptInsertMode } from '../features/run-form/promptInsertion'
import type { RunFormState } from '../features/run-form/types'

/**
 * フォームへの読み込みのリクエスト。画像の生成情報(ADR-0038 9章)、パラメーターセット(ADR-0040)、
 * Run 詳細の「同じ設定で新規作成」(`run`。フォームの状態をそのまま入れる。入力画像とグループも含む)。
 */
export type FormLoadRequest =
  | { kind: 'sdwebui'; response: SdWebuiImportParamsResponse }
  | { kind: 'parameterSet'; set: ParameterSetResponse }
  | { kind: 'run'; state: RunFormState }

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
   * フォームへの読み込みのリクエスト。画像の生成情報から SD WebUI のフォームへ(ADR-0038 9章。
   * ビューアの「SD WebUI のフォームに読み込む」とスタジオの「画像から設定を読み込む」)と、
   * パラメーターセットから(ADR-0040。フォームの「設定を読み込む」とサイドバーのパネル)、
   * Run 詳細の「同じ設定で新規作成」から(スタジオの結果エリアの Run 詳細のように、フォームが
   * マウントされたままでも入るように。`formState` はマウント時の初期値としてしか読まれない)。
   * スタジオ(useRunFormLogic)が capabilities に照らして消費する。プロンプトの置き換えの確認は
   * 積む側で済ませる。nonce は二重消費を防ぐ通し番号。localStorage には保存しない。
   */
  pendingFormLoad: { request: FormLoadRequest; nonce: number } | null
  requestFormLoad: (request: FormLoadRequest) => void
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

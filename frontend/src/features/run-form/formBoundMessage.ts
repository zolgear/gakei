/**
 * 生成のボタンのまわりに出す「その時点のフォームについての」知らせ(モデルの切り替えで外した項目、
 * 領域を外した知らせ、送信して断られたときのエラー)を、フォームの内容が変わったら消す仕組み。
 *
 * 知らせは effect やサーバーの応答のあとで出るので、出した時点のフォームの内容は、その次の描画で
 * 決まる。そこで、出した直後は「まだ内容を覚えていない」(key = null)にし、次の描画で今の内容の
 * キーを覚える。以後、キーが変わったら(利用者がプロンプト・入力・モデル・パラメーター・四角などを
 * 変えたら)消す。送信できない理由(プロンプトが空など)は状態から毎回計算するので、ここでは扱わない。
 */
import { useCallback, useState } from 'react'

export interface FormBoundMessageState<T> {
  value: T | null
  /** 知らせを出したときのフォームの内容のキー。出した直後でまだ覚えていなければ null。 */
  key: string | null
}

/**
 * 今のフォームの内容のキー(formKey)を見て、知らせの状態をどう変えるか。変えなくてよければ同じ
 * オブジェクトを返す(描画中の setState を繰り返さないため)。
 */
export function nextFormBoundMessage<T>(state: FormBoundMessageState<T>, formKey: string): FormBoundMessageState<T> {
  if (state.value === null) return state
  if (state.key === null) return { value: state.value, key: formKey }
  if (state.key !== formKey) return { value: null, key: null }
  return state
}

/**
 * フォームの内容が変わったら消える知らせ。`formKey` はフォームの内容を表す文字列
 * (`runFormContentKey`)。返す値は、内容が変わった描画からもう出さない。
 */
export function useFormBoundMessage<T>(formKey: string): [T | null, (value: T | null) => void] {
  const [state, setState] = useState<FormBoundMessageState<T>>({ value: null, key: null })
  const next = nextFormBoundMessage(state, formKey)
  // 描画中に前回の値と比べて直す(effect で setState しない。React の「前の値を覚える」書き方)。
  if (next !== state) setState(next)
  const set = useCallback((value: T | null) => setState({ value, key: null }), [])
  return [next.value, set]
}

/** フォームの内容のキー。知らせを消すかどうかの比較だけに使う。 */
export function runFormContentKey(content: {
  provider: string
  model: string
  prompt: string
  rawParams: unknown
  sizeState: unknown
  inputs: ReadonlyArray<{ assetId: string; role: string; position: number }>
  regions: unknown
  repeatRaw: string
  assetGroupId: string | null
  promptTemplate: unknown
}): string {
  return JSON.stringify([
    content.provider,
    content.model,
    content.prompt,
    content.rawParams,
    content.sizeState,
    content.inputs.map((i) => [i.assetId, i.role, i.position]),
    content.regions,
    content.repeatRaw,
    content.assetGroupId,
    content.promptTemplate,
  ])
}

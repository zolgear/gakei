/**
 * 生成画面の「ワークフローを見る」(モデルの欄の下。ADR-0052 7章)から、結果エリアの「ワークフロー」の表示を開く
 * ための合図(モジュールスコープ。`studioResultStore.ts` と同じく、入力欄と結果エリアの間をつなぐだけ)。
 *
 * 入力欄(`ModelSelect`)が `requestWorkflowView()` を呼び、結果エリア(`ResultPane`)が購読して表示を切り替える。
 */

type Listener = () => void

const listeners = new Set<Listener>()

/** 結果エリアに「ワークフロー」の表示を開くよう知らせる。 */
export function requestWorkflowView(): void {
  for (const listener of listeners) listener()
}

/** 合図を購読する。戻り値で購読をやめる。 */
export function subscribeWorkflowView(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

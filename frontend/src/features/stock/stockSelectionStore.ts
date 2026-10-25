/**
 * ストックの選択モードで選んでいる Asset の ID の外部ストア(モジュールスコープに1つ。
 * `features/workspace/studioResultStore.ts` と同じパターン)。
 *
 * `StockPanel` が選択の変化を effect で書き込み、選択モードを抜けたとき・パネルが閉じたときに空に
 * 戻す。アシスタントは送信のたびに読み、`form.selected_asset_ids` として送る(ADR-0048 13章)。
 * 送信時に1回読むだけなので購読は持たない。
 */

/** 送る上限(`backend/app/domain/schemas.py` の ASSISTANT_SELECTED_MAX)。 */
export const STOCK_SELECTION_SEND_MAX = 200

let current: readonly string[] = []

export function setStockSelectedAssetIds(assetIds: readonly string[]): void {
  current = [...assetIds]
}

/** 選んでいる Asset の ID(選んだ順、上限まで)。選んでいなければ空。 */
export function getStockSelectedAssetIds(): string[] {
  return current.slice(0, STOCK_SELECTION_SEND_MAX)
}

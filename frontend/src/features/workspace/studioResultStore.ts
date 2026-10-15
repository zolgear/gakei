/**
 * スタジオの結果エリアに出ている Asset の ID の外部ストア(モジュールスコープに1つ。
 * `features/favicon/watchedRunStore.ts` と同じパターン)。
 *
 * `ResultPane` が表示の起点(`resolveResultOrigin`)の Asset ID を effect で書き込み、外れる
 * (スタジオから離れる)ときに null に戻す。アシスタントは送信のたびに読み、
 * `form.result_asset_id` として送る(ADR-0048 12章)。送信時に1回読むだけなので購読は持たない。
 */

let current: string | null = null

export function setStudioResultAssetId(assetId: string | null): void {
  current = assetId
}

/** 結果エリアに出ている Asset の ID。実行中の Run・空・スタジオを開いていないときは null。 */
export function getStudioResultAssetId(): string | null {
  return current
}

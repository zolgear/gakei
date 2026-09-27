/** 同じ Generated(Run)の複数出力間の切り替えにまつわる純粋関数。 */
import type { RunOutputRef } from '../../api/client'

/** output_index 順に並べた出力一覧(サーバーの返す順序に頼らず、念のため並べ替える)。 */
export function sortRunOutputs(outputs: RunOutputRef[]): RunOutputRef[] {
  return [...outputs].sort((a, b) => (a.output_index ?? 0) - (b.output_index ?? 0))
}

export interface RunOutputNav {
  /** 出力一覧を output_index 順に並べたもの。 */
  sorted: RunOutputRef[]
  /** 現在の Asset が何番目か(1始まり)。 */
  position: number
  /** 出力の総数。 */
  total: number
  /** 1つ前の出力の Asset id(先頭なら null)。 */
  previousAssetId: string | null
  /** 1つ次の出力の Asset id(末尾なら null)。 */
  nextAssetId: string | null
}

/**
 * 現在表示している Asset を基準に、切り替えUI用の情報(位置、前後の Asset id)を求める。
 * 出力が1つ以下、または現在の Asset がその出力一覧に含まれない場合は null を返す
 * (呼び出し側はこのとき切り替えUIを出さない)。
 */
export function resolveRunOutputNav(
  outputs: RunOutputRef[],
  currentAssetId: string,
): RunOutputNav | null {
  if (outputs.length < 2) return null
  const sorted = sortRunOutputs(outputs)
  const index = sorted.findIndex((o) => o.asset_id === currentAssetId)
  if (index === -1) return null
  return {
    sorted,
    position: index + 1,
    total: sorted.length,
    previousAssetId: index > 0 ? sorted[index - 1].asset_id : null,
    nextAssetId: index < sorted.length - 1 ? sorted[index + 1].asset_id : null,
  }
}

/**
 * URL の `sel`(Asset ID)を、配置の中の位置に直す。配置ができる前と、その画像が今の配置に
 * 無いときは選ばない(ビューアから戻ったとき、配置が揃ってから選択を戻す)。
 */
import { useMemo } from 'react'
import type { EmbeddingGraphNode } from '../../api/client'

export function useSelectedIndex(
  nodes: EmbeddingGraphNode[],
  selectedId: string | null,
  ready: boolean,
): number | null {
  return useMemo(() => {
    if (!ready || !selectedId) return null
    const index = nodes.findIndex((node) => node.id === selectedId)
    return index >= 0 ? index : null
  }, [nodes, selectedId, ready])
}

/** 選んだ画像へ寄るかどうかを決めるときの入力。 */
export interface SelectionRevealInput {
  /** 配置の座標がもう出ているか。 */
  hasPositions: boolean
  /** その見せ方が、選んだ画像へ寄るか(立体だけ)。 */
  followsSelection: boolean
  /** 直前に覚えていた選択(まだ一度も覚えていなければ undefined)。 */
  prev: number | null | undefined
  /** 今の選択。 */
  selected: number | null
}

/**
 * 選んだ画像へ寄るか、そして今の選択を覚えるかを決める(純関数)。
 *
 * 寄るのは**利用者がその場で選び直したとき**だけ。URL の `sel` は配置ができるまで位置に直せず
 * (`useSelectedIndex`)、開いた直後は null → 位置と 2 段階で届くので、座標が出そろうまでは
 * 覚えも寄りもしない。ここを取り違えると、タブを切り替えた直後に**計算の途中の座標**へ寄って
 * しまい、そのあと配置が動いても追わないので、何も無い所を映したままになる。
 */
export function selectionRevealStep(input: SelectionRevealInput): { record: boolean; reveal: boolean } {
  if (!input.hasPositions) return { record: false, reveal: false }
  const { followsSelection, prev, selected } = input
  const reveal = followsSelection && prev !== undefined && selected !== null && prev !== selected
  return { record: true, reveal }
}

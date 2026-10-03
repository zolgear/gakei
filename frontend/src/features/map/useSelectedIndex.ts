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

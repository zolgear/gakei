/**
 * 系列グラフのインスペクターに何を出すか(Run の詳細 / Asset のプレビュー / 埋め込み
 * (未検証)ノードの内容 / 何も出さない)を選択中ノード id とグラフのノード一覧から決める
 * 純粋関数。存在しない id(まだ読み込み中、または別の起点に切り替わって消えたノード)は
 * 'none' にする。
 *
 * 埋め込み(未検証)ノード(ADR-0014 6章、`LineageNode.embedded`)は DB に行が無いため、
 * `getAsset`/`getRun` を呼ばず ノード自体(`embedded_detail` を含む)をそのままインスペクター
 * へ渡す(`EmbeddedNodeInspector` が API を呼ばずに描画できるようにする)。
 */
import type { LineageNode } from '../../api/client'

export interface InspectorGraphNode {
  id: string
  type: 'asset' | 'run'
}

export type InspectorContent =
  | { kind: 'run'; id: string }
  | { kind: 'asset'; id: string }
  | { kind: 'embedded'; node: LineageNode }
  | { kind: 'none' }

export function resolveInspectorContent(
  nodeId: string | null,
  nodes: InspectorGraphNode[] | LineageNode[],
): InspectorContent {
  if (!nodeId) return { kind: 'none' }
  const node = nodes.find((n) => n.id === nodeId)
  if (!node) return { kind: 'none' }
  if ('embedded' in node && node.embedded) return { kind: 'embedded', node: node as LineageNode }
  return node.type === 'run' ? { kind: 'run', id: node.id } : { kind: 'asset', id: node.id }
}

/**
 * インスペクターの「Generated の詳細を開く」(Asset の生成元 Run)の開き方を決める。同じグラフに
 * その Run のノードがあれば、ページを移らずインスペクターをその Run に切り替える('select'。URL の
 * `?node=` が変わるので、戻る操作で元の Asset に戻れる)。無ければ Run 詳細ページへ移る('page')。
 */
export function resolveOpenRunTarget(
  runId: string,
  nodes: InspectorGraphNode[] | LineageNode[],
): { kind: 'select'; nodeId: string } | { kind: 'page'; path: string } {
  const node = nodes.find((n) => n.id === runId && n.type === 'run')
  if (node && !('embedded' in node && node.embedded)) return { kind: 'select', nodeId: runId }
  return { kind: 'page', path: `/runs/${runId}` }
}

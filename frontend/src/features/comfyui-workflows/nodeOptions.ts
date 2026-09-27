/**
 * analyze の `nodes`(テンプレートのノード一覧)から、差し込み先を選ぶ UI の選択肢を作る
 * 純粋関数(ADR-0013)。DB やネットワークには依存しない。
 */
import type { ComfyNodeInfo, ComfyNodeInputInfo } from '../../api/client'
import { fmt, msg } from '../../i18n'

export interface InputRefValue {
  node: string
  input: string
}

/** ノード選択肢の表示ラベル: "ID: class_type — title"(title が無ければ省く)。 */
export function nodeOptionLabel(node: ComfyNodeInfo): string {
  const title = node.title ? ` — ${node.title}` : ''
  return `${node.id}: ${node.class_type ?? '?'}${title}`
}

/** 入力選択肢の表示ラベル。配線済みの入力は上書きになることが分かるよう明示する。 */
export function inputOptionLabel(input: ComfyNodeInputInfo): string {
  return input.linked ? fmt(msg().comfyui.nodePicker.wired, { name: input.name }) : input.name
}

export function findNode(nodes: ComfyNodeInfo[], nodeId: string | null | undefined): ComfyNodeInfo | undefined {
  if (!nodeId) return undefined
  return nodes.find((n) => n.id === nodeId)
}

export function nodeExists(nodeId: string, nodes: ComfyNodeInfo[]): boolean {
  return nodes.some((n) => n.id === nodeId)
}

/** 指定の (node, input) がテンプレート(analyze の nodes)にまだ存在するか。 */
export function refExistsInNodes(ref: InputRefValue | null | undefined, nodes: ComfyNodeInfo[]): boolean {
  if (!ref) return true
  const node = findNode(nodes, ref.node)
  if (!node) return false
  return (node.inputs ?? []).some((i) => i.name === ref.input)
}

export function inputsForNode(nodes: ComfyNodeInfo[], nodeId: string | null | undefined): ComfyNodeInputInfo[] {
  return findNode(nodes, nodeId)?.inputs ?? []
}

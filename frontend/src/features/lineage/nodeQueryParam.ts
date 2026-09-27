/**
 * `/lineage/:assetId?node=<id>` の URL 同期用の純粋関数。インスペクターで選んでいる
 * ノード(Asset/Run 共通の id)を戻る/進むで再現する。
 */

export function parseNodeIdFromSearch(search: string): string | null {
  const raw = new URLSearchParams(search).get('node')
  const trimmed = raw?.trim() ?? ''
  return trimmed.length > 0 ? trimmed : null
}

export function buildLineagePath(assetId: string, nodeId: string | null): string {
  if (!nodeId) return `/lineage/${assetId}`
  const params = new URLSearchParams({ node: nodeId })
  return `/lineage/${assetId}?${params.toString()}`
}

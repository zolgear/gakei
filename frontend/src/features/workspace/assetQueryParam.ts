/**
 * `/studio?asset=<id>&node=<id>&run=<id>` の URL 同期用の純粋関数。表示中の Asset(`asset`)、
 * 系列モードでインスペクターに開いているノード(`node`。`../lineage/nodeQueryParam` と同じキー)、
 * 進捗を表示中の Run(`run`。ADR-0009「生成中の Run を開き直す」2026-09-23)を戻る/進むで再現する。
 */

export function parseAssetIdFromSearch(search: string): string | null {
  const raw = new URLSearchParams(search).get('asset')
  const trimmed = raw?.trim() ?? ''
  return trimmed.length > 0 ? trimmed : null
}

/** `?run=` を取り出す(ADR-0009「生成中の Run を開き直す」2026-09-23)。 */
export function parseRunIdFromSearch(search: string): string | null {
  const raw = new URLSearchParams(search).get('run')
  const trimmed = raw?.trim() ?? ''
  return trimmed.length > 0 ? trimmed : null
}

export function buildStudioPath(
  assetId: string | null,
  nodeId?: string | null,
  runId?: string | null,
): string {
  const params = new URLSearchParams()
  if (assetId) params.set('asset', assetId)
  if (runId) params.set('run', runId)
  if (nodeId) params.set('node', nodeId)
  const qs = params.toString()
  return qs ? `/studio?${qs}` : '/studio'
}

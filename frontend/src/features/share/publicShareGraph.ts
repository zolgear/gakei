/**
 * 共有のページの応答(`PublicShareResponse`)を、系列グラフの部品(`LineageGraph.tsx` の
 * `buildFlowGraph`)が読む形(`AssetLineageResponse`)に直す。応答に無い項目(削除済み、埋め込み
 * など)は既定値で埋める。Run の深さは、その Run が作った画像のうち最も浅いものの1つ上。
 */
import type {
  AssetLineageResponse,
  LineageEdge,
  LineageNode,
  PublicShareResponse,
} from '../../api/client'

export function toLineageResponse(data: PublicShareResponse): AssetLineageResponse {
  const runDepth = new Map<string, number>()
  for (const asset of data.assets ?? []) {
    if (!asset.run_id) continue
    const depth = (asset.depth ?? 0) - 1
    const prev = runDepth.get(asset.run_id)
    runDepth.set(asset.run_id, prev === undefined ? depth : Math.min(prev, depth))
  }

  const nodes: LineageNode[] = [
    ...(data.assets ?? []).map(
      (a): LineageNode => ({
        id: a.id,
        type: 'asset',
        depth: a.depth ?? 0,
        deleted: false,
        embedded: false,
        local_hidden: false,
        asset: { kind: a.kind, width: a.width, height: a.height, mime: a.mime, restorable: false },
      }),
    ),
    ...(data.runs ?? [])
      .filter((r) => runDepth.has(r.id))
      .map(
        (r): LineageNode => ({
          id: r.id,
          type: 'run',
          depth: runDepth.get(r.id) ?? -1,
          deleted: false,
          embedded: false,
          local_hidden: false,
          run: {
            operation: r.operation,
            model: r.model,
            status: 'succeeded',
            prompt: r.prompt,
            queued_at: r.created_at,
            // 共有のページは取り込みの印を出さない(公開の応答に持たない)。
            imported: false,
          },
        }),
      ),
  ]
  const ids = new Set(nodes.map((n) => n.id))
  const edges: LineageEdge[] = (data.edges ?? [])
    .filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({
      source: e.source,
      target: e.target,
      kind: e.kind,
      role: e.role ?? null,
      position: e.position ?? null,
      output_index: e.output_index ?? null,
      primary: e.primary ?? false,
    }))
  return { root_asset_id: data.root_asset_id, nodes, edges, truncated: false }
}

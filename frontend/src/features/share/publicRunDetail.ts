/**
 * 共有のページの Run(Generated)の詳細に出すものを、公開の応答(`PublicShareResponse`)から
 * 組み立てる純粋関数(ADR-0029 3章、2026-09-30 追記)。
 *
 * - 応答の `runs` に無い Run は null(画面は「このリンクは無効です」を出す)。サーバーは失敗・
 *   取り消しの Run と、共有に含まれる画像を作っていない Run を返さない。
 * - 入力・出力は、共有に含まれる画像(応答の `assets`)だけ。範囲外のものは数えない(枚数も出さない)。
 * - 入力は主たる親(`position = 0` の `image`)と、参照(ほかの入力画像、マスク、スケッチなど)に分ける。
 * - 「編集前と比較する」は、Edit の Run で主たる親と出力の両方が共有に含まれるときだけ。
 */
import type { PublicShareAsset, PublicShareResponse, PublicShareRun } from '../../api/client'

export interface PublicRunInput {
  asset: PublicShareAsset
  role: 'image' | 'mask' | 'reference'
  position: number
}

export interface PublicRunOutput {
  asset: PublicShareAsset
  outputIndex: number | null
}

export interface PublicRunDetail {
  run: PublicShareRun
  primaryParent: PublicRunInput | null
  references: PublicRunInput[]
  outputs: PublicRunOutput[]
}

export function buildPublicRunDetail(data: PublicShareResponse, runId: string): PublicRunDetail | null {
  const run = (data.runs ?? []).find((r) => r.id === runId)
  if (!run) return null
  const assetsById = new Map((data.assets ?? []).map((a) => [a.id, a]))

  const inputs: PublicRunInput[] = []
  const outputs: PublicRunOutput[] = []
  const seenOutputs = new Set<string>()
  for (const edge of data.edges ?? []) {
    if (edge.kind === 'input' && edge.target === runId) {
      const asset = assetsById.get(edge.source)
      if (!asset) continue
      inputs.push({ asset, role: edge.role ?? 'image', position: edge.position ?? 0 })
    } else if (edge.kind === 'output' && edge.source === runId) {
      const asset = assetsById.get(edge.target)
      if (!asset || seenOutputs.has(asset.id)) continue
      seenOutputs.add(asset.id)
      outputs.push({ asset, outputIndex: edge.output_index ?? null })
    }
  }
  // 辺が欠けていても、画像の側の run_id から出力を拾う(出力の辺と同じ意味)。
  for (const asset of data.assets ?? []) {
    if (asset.run_id === runId && !seenOutputs.has(asset.id)) {
      seenOutputs.add(asset.id)
      outputs.push({ asset, outputIndex: null })
    }
  }

  inputs.sort((a, b) => a.position - b.position || a.role.localeCompare(b.role))
  outputs.sort((a, b) => (a.outputIndex ?? Number.MAX_SAFE_INTEGER) - (b.outputIndex ?? Number.MAX_SAFE_INTEGER))

  const primaryIndex = inputs.findIndex((i) => i.role === 'image' && i.position === 0)
  const primaryParent = primaryIndex >= 0 ? inputs[primaryIndex] : null
  const references = inputs.filter((_, index) => index !== primaryIndex)
  return { run, primaryParent, references, outputs }
}

/**
 * 「編集前と比較する」で並べる2枚。Edit で主たる親と出力が共有に含まれるときだけ。出力は
 * いまビューアで開いている画像がこの Run の出力ならそれ、ほかは先頭の出力。
 */
export function publicCompareTargets(
  detail: PublicRunDetail,
  selectedAssetId: string | null,
): { before: PublicShareAsset; after: PublicShareAsset } | null {
  if (detail.run.operation !== 'edit' || !detail.primaryParent || detail.outputs.length === 0) return null
  const after = detail.outputs.find((o) => o.asset.id === selectedAssetId) ?? detail.outputs[0]
  return { before: detail.primaryParent.asset, after: after.asset }
}

/**
 * Run の詳細を開いたときにビューアに出す画像。いま開いている画像がこの Run の出力ならそのまま、
 * ほかは先頭の出力(無ければ主たる親、それも無ければ今のまま)。
 */
export function assetForRun(detail: PublicRunDetail, currentAssetId: string | null): string | null {
  if (currentAssetId && detail.outputs.some((o) => o.asset.id === currentAssetId)) return currentAssetId
  return detail.outputs[0]?.asset.id ?? detail.primaryParent?.asset.id ?? currentAssetId
}

/**
 * この画像の原本を出せるか(ADR-0029 4章)。`allowed` 以外は、プレビューまで(原本のダウンロード
 * は出さない)。共有が原本を許していても、秘密に見える値を含む ComfyUI の Run の画像はサーバーが
 * 画像ごとに `allow_original: false` を返す(2026-10-01 追記)。
 * - `share`: 共有が原本を許していない
 * - `image`: 共有は許しているが、この画像は出さない
 */
export function originalAvailability(
  share: Pick<PublicShareResponse, 'allow_original'>,
  asset: Pick<PublicShareAsset, 'allow_original'>,
): 'allowed' | 'share' | 'image' {
  if (!share.allow_original) return 'share'
  return asset.allow_original ? 'allowed' : 'image'
}

/** 比較で原本まで拡大してよいか(比べる2枚のどちらも原本を出せるときだけ)。 */
export function compareAllowsOriginal(
  share: Pick<PublicShareResponse, 'allow_original'>,
  targets: { before: Pick<PublicShareAsset, 'allow_original'>; after: Pick<PublicShareAsset, 'allow_original'> },
): boolean {
  return (
    originalAvailability(share, targets.before) === 'allowed' && originalAvailability(share, targets.after) === 'allowed'
  )
}

export type PublicParamScalar = string | number | boolean | null

/**
 * Run の params を、一覧に出すスカラーの値と、畳んだ JSON のブロックで出す入れ子の値に分ける
 * (ADR-0029 3章、2026-10-01 改訂)。ComfyUI の Run は送ったグラフ全体(`comfyui_prompt`)など
 * 入れ子の値を含む。並びは params のキーの順のまま。
 */
export function splitPublicParams(params: Record<string, unknown> | null | undefined): {
  scalars: [string, PublicParamScalar][]
  nested: [string, unknown][]
} {
  const scalars: [string, PublicParamScalar][] = []
  const nested: [string, unknown][] = []
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      scalars.push([key, value])
    } else if (value !== undefined) {
      nested.push([key, value])
    }
  }
  return { scalars, nested }
}

/**
 * 共有の画像が、生成元の Run の何番目の出力か(`output` の辺の `output_index`)。無ければ null。
 * 公開の Asset は `output_index` を持たないので、辺から引く(展開後のプロンプトの選択に使う。ADR-0038 7章)。
 */
export function publicOutputIndex(data: PublicShareResponse, runId: string, assetId: string): number | null {
  const edge = (data.edges ?? []).find((e) => e.kind === 'output' && e.source === runId && e.target === assetId)
  return edge?.output_index ?? null
}

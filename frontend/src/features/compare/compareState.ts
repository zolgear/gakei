/**
 * 比較ビュー(`/runs/:runId/compare`)の URL 同期・既定選択にまつわる純粋関数。
 * ADR-0009「7. 編集前との比較」参照。
 */
import type { RunDetail } from '../../api/client'
import type { FocalPointValue } from '../../lib/focalPoint'

export type CompareMode = 'side' | 'slider'

export interface CompareSearchState {
  before: string | null
  after: string | null
  mode: CompareMode | null
}

/** `?before=&after=&mode=` を読む。値が無い/空文字は null。 */
export function parseCompareSearch(search: string): CompareSearchState {
  const params = new URLSearchParams(search)
  const before = params.get('before')?.trim() || null
  const after = params.get('after')?.trim() || null
  const rawMode = params.get('mode')?.trim() || null
  const mode: CompareMode | null = rawMode === 'side' || rawMode === 'slider' ? rawMode : null
  return { before, after, mode }
}

/** 比較ビューへのパス(クエリ含む)を組み立てる。 */
export function buildComparePath(runId: string, state: CompareSearchState): string {
  const params = new URLSearchParams()
  if (state.before) params.set('before', state.before)
  if (state.after) params.set('after', state.after)
  if (state.mode) params.set('mode', state.mode)
  const qs = params.toString()
  return `/runs/${runId}/compare${qs ? `?${qs}` : ''}`
}

export interface CompareCandidate {
  assetId: string
  label: string
}

/** 比較対象として選べる入力(マスクを除く role=image/reference)。 */
export function compareInputCandidates(
  run: Pick<RunDetail, 'inputs'>,
): {
  assetId: string
  role: 'image' | 'reference'
  position: number
  focalPoint: FocalPointValue | null
}[] {
  return (run.inputs ?? [])
    .filter((i) => i.role === 'image' || i.role === 'reference')
    .map((i) => ({
      assetId: i.asset_id,
      role: i.role as 'image' | 'reference',
      position: i.position,
      focalPoint: i.focal_point ?? null,
    }))
}

/** 比較対象として選べる出力。 */
export function compareOutputCandidates(
  run: Pick<RunDetail, 'outputs'>,
): { assetId: string; outputIndex: number | null; focalPoint: FocalPointValue | null }[] {
  return (run.outputs ?? []).map((o) => ({
    assetId: o.asset_id,
    outputIndex: o.output_index,
    focalPoint: o.focal_point ?? null,
  }))
}

/** 既定の「主たる親」(role=image, position=0)の asset id。無ければ null。 */
export function primaryParentAssetId(run: Pick<RunDetail, 'inputs'>): string | null {
  const primary = (run.inputs ?? []).find((i) => i.role === 'image' && i.position === 0)
  return primary?.asset_id ?? null
}

export interface ResolvedComparison {
  before: string | null
  after: string | null
}

/**
 * 比較する入力(before)・出力(after)を決める。URL の指定が候補に含まれていればそれを使い、
 * 無効/未指定なら既定(主たる親 / 先頭の出力)に落ちる。
 */
export function resolveComparison(
  run: Pick<RunDetail, 'inputs' | 'outputs'>,
  requested: { before: string | null; after: string | null },
): ResolvedComparison {
  const inputIds = compareInputCandidates(run).map((i) => i.assetId)
  const outputIds = compareOutputCandidates(run).map((o) => o.assetId)

  const before =
    requested.before && inputIds.includes(requested.before)
      ? requested.before
      : (primaryParentAssetId(run) ?? inputIds[0] ?? null)

  const after = requested.after && outputIds.includes(requested.after) ? requested.after : (outputIds[0] ?? null)

  return { before, after }
}

/** 768px 未満では並べて表示を出さず、常にスライダーにする。 */
export function resolveEffectiveMode(requestedMode: CompareMode | null, isMobile: boolean): CompareMode {
  if (isMobile) return 'slider'
  return requestedMode === 'slider' ? 'slider' : 'side'
}

/** Edit の出力かどうか(Generate の出力には比較の入口を出さない)。 */
export function hasComparableInput(run: Pick<RunDetail, 'inputs'>): boolean {
  return compareInputCandidates(run).length > 0
}

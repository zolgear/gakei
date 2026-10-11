/**
 * Run 詳細などに SD WebUI 由来の項目(ADR-0038 3章)をどう出すかを決める純粋関数。
 * - `run.params` の `sdwebui_request`(送った本文全体。大きい)と `sdwebui_task_id`(進み具合の
 *   問い合わせ用の ID。`provider_request_id` と同じ)は、パラメーターの一覧に生で並べず、
 *   送った本文は折りたたみの JSON で見せる。
 * - seed は `sdwebui_seed`(実際に使った seed)を見せる。WebUI は1枚ごとに別の seed を使うので、
 *   複数枚の Run では `usage.all_seeds` から何枚目がどの seed かを示す。
 * - `usage.infotext`(WebUI が書く生成情報。1枚目)は、折りたたみで見せる。
 */
import { fmt, msg } from '../../i18n'

/** `run.params` から、実際に使った seed(`sdwebui_seed`)を取り出す。無ければ null。 */
export function extractSdWebuiSeed(params: Record<string, unknown>): number | null {
  const value = params.sdwebui_seed
  return typeof value === 'number' ? value : null
}

/** `run.params` から、WebUI に送った本文(`sdwebui_request`)を取り出す。無ければ null。 */
export function extractSdWebuiRequest(params: Record<string, unknown>): Record<string, unknown> | null {
  const value = params.sdwebui_request
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

/** `run.usage` から、1枚ごとの seed(`all_seeds`)を取り出す。数の配列でなければ null。 */
export function extractSdWebuiAllSeeds(usage: Record<string, unknown> | null | undefined): number[] | null {
  const value = usage?.all_seeds
  if (!Array.isArray(value) || value.length === 0) return null
  return value.every((v) => typeof v === 'number') ? (value as number[]) : null
}

/** `run.usage` から、WebUI の生成情報(`infotext`)を取り出す。空なら null。 */
export function extractSdWebuiInfotext(usage: Record<string, unknown> | null | undefined): string | null {
  const value = usage?.infotext
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/**
 * seed の表示。
 * - 1枚: `seed 123`
 * - 複数枚で、特定の出力を見ているとき(`outputIndex`): その出力の seed と、何枚目か
 * - 複数枚で、Run 全体を見ているとき: 1枚ごとの seed を並べる
 * `all_seeds` が無い(実行前・失敗など)ときは、`sdwebui_seed` だけで示す。
 */
export function describeSdWebuiSeed(
  seed: number | null,
  allSeeds: number[] | null,
  outputCount: number,
  outputIndex?: number | null,
): string | null {
  if (seed === null && allSeeds === null) return null
  const t = msg().runDetail
  const first = allSeeds?.[0] ?? seed
  if (outputCount <= 1) return fmt(t.seed.single, { seed: first ?? '' })
  if (outputIndex !== null && outputIndex !== undefined) {
    const own = allSeeds?.[outputIndex]
    if (own !== undefined) {
      return fmt(t.sdwebuiSeed.indexed, { seed: own, outputCount, outputIndex: outputIndex + 1 })
    }
  }
  if (allSeeds && allSeeds.length > 1) return fmt(t.sdwebuiSeed.batch, { seeds: allSeeds.join(', ') })
  return fmt(t.seed.single, { seed: first ?? '' })
}

/** seed の表示に添えるツールチップ。枚数が2以上のときだけ。 */
export function sdWebuiSeedTooltip(outputCount: number): string | undefined {
  return outputCount > 1 ? msg().runDetail.sdwebuiSeed.tooltip : undefined
}

/** 「JSON をダウンロード」用のファイル名。 */
export function sdwebuiRequestDownloadFilename(runId: string): string {
  return `sdwebui-request-${runId}.json`
}

/** usage の JSON に出すもの。infotext は別の折りたたみで見せるので除く。 */
export function usageWithoutInfotext(usage: Record<string, unknown>): Record<string, unknown> {
  if (!('infotext' in usage)) return usage
  const rest = { ...usage }
  delete rest.infotext
  return rest
}

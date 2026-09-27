/**
 * Run 詳細の「詳細設定」に ComfyUI 由来の項目(ADR-0013)をどう出すかを決める純粋関数。
 * `run.params` の `comfyui_*` は、値を差し込んだグラフ全体(`comfyui_prompt`)を含み丸ごと
 * JSON にすると読みにくいので、通常のパラメータ一覧・生JSONブロックからは除き、代わりに
 * ワークフロー名 + sha の先頭・折りたたみJSON・ダウンロードとして別枠で表示する。
 */
import { fmt, msg } from '../../i18n'

export interface ComfyUiWorkflowInfo {
  id: string
  name: string
  template_sha256: string
}

function isComfyUiWorkflowInfo(value: unknown): value is ComfyUiWorkflowInfo {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return typeof v.id === 'string' && typeof v.name === 'string' && typeof v.template_sha256 === 'string'
}

/** `run.params` から `comfyui_workflow` を取り出す。形が合わなければ null。 */
export function extractComfyUiWorkflowInfo(params: Record<string, unknown>): ComfyUiWorkflowInfo | null {
  const value = params.comfyui_workflow
  return isComfyUiWorkflowInfo(value) ? value : null
}

/** `run.params` から、埋め込み後のグラフ全体(`comfyui_prompt`)を取り出す。無ければ null。 */
export function extractComfyUiPrompt(params: Record<string, unknown>): Record<string, unknown> | null {
  const value = params.comfyui_prompt
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

/** `run.params` から、実際に使った seed(`comfyui_seed`)を取り出す。無ければ null。 */
export function extractComfyUiSeed(params: Record<string, unknown>): number | null {
  const value = params.comfyui_seed
  return typeof value === 'number' ? value : null
}

/**
 * seed の表示。ComfyUI は1つの seed からバッチ全体のノイズを作るので、枚数が2以上の Run では
 * 1枚を再現するのに「seed + 枚数 + 何枚目か」が要る。そのことが分かる形にする(ADR-0013)。
 * `outputIndex` は、特定の出力(Asset)を見ているときだけ渡す。
 */
export function describeComfyUiSeed(
  seed: number | null,
  outputCount: number,
  outputIndex?: number | null,
): string | null {
  if (seed === null) return null
  const t = msg().runDetail.seed
  if (outputCount <= 1) return fmt(t.single, { seed })
  if (outputIndex === null || outputIndex === undefined) return fmt(t.batch, { seed, outputCount })
  return fmt(t.indexed, { seed, outputCount, outputIndex: outputIndex + 1 })
}

/** seed の表示に添えるツールチップ。枚数が2以上のときだけ。 */
export function comfyUiSeedTooltip(outputCount: number): string | undefined {
  return outputCount > 1 ? msg().runDetail.seed.tooltip : undefined
}

/** sha256 の先頭を短縮表示する(既定12文字。ADR-0013)。 */
export function shortSha256(sha: string, length = 12): string {
  return sha.slice(0, length)
}

/** 「JSON をダウンロード」用のファイル名。 */
export function comfyuiPromptDownloadFilename(runId: string): string {
  return `comfyui-prompt-${runId}.json`
}

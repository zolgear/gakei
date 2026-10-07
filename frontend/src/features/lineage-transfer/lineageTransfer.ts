/**
 * 系列の持ち出しと取り込み(ADR-0037)の、画面に依存しない小さな関数。
 */
import type {
  LineageExportMode,
  LineageExportPreviewResponse,
  LineageExportScope,
} from '../../api/client'

/**
 * 書き出しの範囲の並び。「この画像と祖先」「系列全体」は共有リンクの「祖先まで」「祖先と子孫」と
 * 同じ計算。「この画像と子孫」は書き出しだけにある(ADR-0037 1章)。
 */
export const LINEAGE_EXPORT_SCOPES: readonly LineageExportScope[] = ['ancestors', 'descendants', 'lineage']

/** 書き出しの用途の並び(GAKEI に取り込む / 納品用。ADR-0037 4章)。 */
export const LINEAGE_EXPORT_MODES: readonly LineageExportMode[] = ['import', 'delivery']

/** 取り込める ZIP の大きさの上限(サーバーの `lineage_import.MAX_ZIP_BYTES` と同じ 1 GiB)。 */
export const MAX_LINEAGE_ZIP_BYTES = 1024 * 1024 * 1024

export type LineageZipCheck = 'ok' | 'notZip' | 'tooLarge' | 'empty'

/** 送る前の簡単な確かめ(拡張子か MIME が ZIP、大きさ)。中身の検証はサーバーが行う。 */
export function checkLineageZipFile(file: { name: string; type: string; size: number }): LineageZipCheck {
  const type = file.type.toLowerCase()
  const looksZip =
    file.name.toLowerCase().endsWith('.zip') ||
    type === 'application/zip' ||
    type === 'application/x-zip-compressed'
  if (!looksZip) return 'notZip'
  if (file.size <= 0) return 'empty'
  if (file.size > MAX_LINEAGE_ZIP_BYTES) return 'tooLarge'
  return 'ok'
}

/** 送信の進み具合(0〜1)を 0〜100 の整数にする。範囲外や NaN は端に寄せる。 */
export function progressPercent(fraction: number): number {
  if (!Number.isFinite(fraction) || fraction <= 0) return 0
  if (fraction >= 1) return 100
  return Math.floor(fraction * 100)
}

/**
 * 取り込みが終わったあと、取り込んだ画像のビューアへ移るときに、ルーターの state で渡す
 * トーストの文言。ストックのパネルはスマホでは引き出しの中にあり、移った時点で閉じる(トーストも
 * 消える)ので、表示はビューアに任せる。
 */
export interface ImportNavigationState {
  lineageImportToast: string
}

export function importNavigationState(message: string): ImportNavigationState {
  return { lineageImportToast: message }
}

/** ルーターの state から取り込みのトーストの文言を取り出す(無ければ null)。 */
export function importToastFromState(state: unknown): string | null {
  if (!state || typeof state !== 'object') return null
  const value = (state as Record<string, unknown>).lineageImportToast
  return typeof value === 'string' && value !== '' ? value : null
}

/** 書き出し前の系列のプレビュー(ADR-0037 1章、2026-10-07 追記)の見出しに出す数。 */
export interface ExportPreviewGraphSummary {
  /** グラフの Asset ノードの数(= ZIP に入る画像の数)。 */
  assetCount: number
  /** グラフの Run ノードの数(= ZIP に入る Generated の数)。 */
  runCount: number
  /** 範囲の外なので含めない入力の数。 */
  omittedInputCount: number
  truncated: boolean
}

/**
 * プレビューの応答から見出しの数を作る。数はグラフのノードから数える(画面に描いたものと
 * 見出しの数を一致させる。サーバーはグラフと manifest を同じ材料から作るので、
 * `asset_count` / `run_count` とも一致する)。グラフが無ければ null。
 */
export function exportPreviewGraphSummary(
  response: LineageExportPreviewResponse | undefined,
): ExportPreviewGraphSummary | null {
  const graph = response?.graph
  if (!response || !graph) return null
  const nodes = graph.nodes ?? []
  return {
    assetCount: nodes.filter((n) => n.type === 'asset').length,
    runCount: nodes.filter((n) => n.type === 'run').length,
    omittedInputCount: response.omitted_input_count,
    truncated: response.truncated || graph.truncated,
  }
}

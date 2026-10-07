/**
 * 系列の持ち出しと取り込み(ADR-0037)の、画面に依存しない小さな関数。
 */
import type { LineageExportMode, LineageExportScope } from '../../api/client'

/** 書き出しの範囲の並び(共有リンクの「祖先まで」「祖先と子孫」と同じ計算)。 */
export const LINEAGE_EXPORT_SCOPES: readonly LineageExportScope[] = ['ancestors', 'lineage']

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

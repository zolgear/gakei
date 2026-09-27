/** 表示用の細々としたフォーマッタ。複数の画面で共有する。 */
import type { RunStatus } from '../api/client'
import { fmt, intlLocale, msg } from '../i18n'

export function statusLabel(status: RunStatus): string {
  return msg().common.statusLabels[status] ?? status
}

/** API はオフセット付き ISO8601(UTC)を返す。表示はブラウザのローカル時刻にする。 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(intlLocale())
}

export function formatDuration(
  startIso: string | null | undefined,
  endIso: string | null | undefined,
): string {
  if (!startIso || !endIso) return '-'
  const start = new Date(startIso).getTime()
  const end = new Date(endIso).getTime()
  if (Number.isNaN(start) || Number.isNaN(end)) return '-'
  const seconds = Math.max(0, (end - start) / 1000)
  return fmt(msg().common.durationSeconds, { seconds: seconds.toFixed(1) })
}

/** "gpt-image-2.5-sunburst" -> "2.5-sunburst" のように、モデル名の冗長な接頭辞を省く。 */
export function shortenModelName(model: string): string {
  const prefix = 'gpt-image-'
  return model.startsWith(prefix) ? model.slice(prefix.length) : model
}

/** 実行中カードの「n秒経過」表示。`now` は注入可能にしてテストしやすくする。 */
export function formatElapsedSeconds(
  startIso: string | null | undefined,
  now: Date = new Date(),
): string | null {
  if (!startIso) return null
  const start = new Date(startIso).getTime()
  if (Number.isNaN(start)) return null
  const seconds = Math.max(0, (now.getTime() - start) / 1000)
  return fmt(msg().common.elapsedSeconds, { seconds: Math.round(seconds) })
}

/**
 * Asset の種類の表示ラベル。系列グラフの埋め込み(未検証)ノード(ADR-0014 6章)は
 * `kind` が読み取れないことがあるため、未知の値・null/undefined では undefined を返す。
 */
export function assetKindLabel(kind: string | null | undefined): string | undefined {
  if (kind === null || kind === undefined) return undefined
  const labels = msg().common.assetKindLabels as Record<string, string>
  return labels[kind]
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`
}

/**
 * SSE の `progress` イベント(ADR-0013。ComfyUI 等がステップ進捗を送るプロバイダーで届く。
 * OpenAI/Fake では届かない)から、進捗バーの割合(0〜100)を求める純粋関数。
 */
import type { RunEvent } from '../../api/client'

export function progressPercent(event: Pick<RunEvent, 'value' | 'max'>): number {
  const value = event.value ?? 0
  const max = event.max ?? 0
  if (max <= 0) return 0
  return Math.min(100, Math.max(0, (value / max) * 100))
}

/**
 * 途中経過の画像は、出力(`output_index`)ごとに最新の1枚だけを表示する。ComfyUI はプレビューを
 * 数十枚送るので、全部を並べると結果エリアからはみ出す。戻り値は `output_index` の昇順。
 */
export function latestPartialsPerOutput<T extends Pick<RunEvent, 'index' | 'output_index'>>(
  partials: readonly T[],
): T[] {
  const latest = new Map<number, T>()
  for (const event of partials) {
    const key = event.output_index ?? 0
    const current = latest.get(key)
    if (current === undefined || (event.index ?? 0) >= (current.index ?? 0)) latest.set(key, event)
  }
  return [...latest.entries()].sort(([a], [b]) => a - b).map(([, event]) => event)
}

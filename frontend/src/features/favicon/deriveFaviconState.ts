/**
 * favicon / App バーのロゴに「今どの意味の状態を見せるか」を決める純粋関数(ADR-0009 8章)。
 * 入力は「見ている Run」(生成画面が開いていない・表示中の Asset に対応する Run が無ければ null。
 * `watchedRunStore.ts` の形をそのまま使う)と「キュー件数」(App バーのバッジと同じ
 * `shell/queueStatus.ts` の `QueueCounts`)。
 *
 * ルール(ADR-0009 8章の表):
 * - 見ている Run が無い: running>0 → spin、queued>0 → queued、どちらも0 → idle。
 * - 見ている Run がある:
 *   - status=queued → queued。
 *   - status=running かつ connection=error → spin(進捗不明として回転に落とす)。
 *   - status=running: progress があれば value/max×4 を、無く expectedPartials があれば
 *     3×partialCount/expectedPartials を、tiles(0〜3)として使う。どちらも無ければ spin。
 *   - status=succeeded → done、status=failed → error。ただし、この Run が実行中・待機中
 *     だったのをこのタブで見たとき(observedActive)だけ。完了済みの Run を開き直しただけなら
 *     演出を出さず、キュー件数の規則に戻る。
 *   - status=canceled(または不明)→ 見ている Run が終端になった扱いで、キュー件数の規則に戻る
 *     (done と error は終端でも優先する。canceled はキュー待ちしか無いので専用の表現は無い)。
 * - **単調増加**: 同じ Run の間、tiles は前回より減らない。`previousTiles`(呼び出し側が
 *   runId ごとに保持する、直近の tiles。runId が変わったら null)との max を取る。
 */
import type { QueueCounts } from '../../shell/queueStatus'
import type { WatchedRunInfo } from './watchedRunStore'

export type FaviconState =
  | { kind: 'idle' }
  | { kind: 'queued' }
  | { kind: 'spin' }
  | { kind: 'fill'; tiles: 0 | 1 | 2 | 3 }
  | { kind: 'done' }
  | { kind: 'error' }

function fromQueueCounts(queue: QueueCounts): FaviconState {
  if (queue.running > 0) return { kind: 'spin' }
  if (queue.queued > 0) return { kind: 'queued' }
  return { kind: 'idle' }
}

function clampTiles(raw: number): 0 | 1 | 2 | 3 {
  return Math.max(0, Math.min(3, Math.floor(raw))) as 0 | 1 | 2 | 3
}

/** progress(優先)か partial の届いた件数から tiles(0〜3)を求める。どちらも使えなければ null。 */
function computeTiles(watched: WatchedRunInfo, previousTiles: number | null): 0 | 1 | 2 | 3 | null {
  let raw: number | null = null
  if (watched.progress !== null && watched.progress.max > 0) {
    raw = (watched.progress.value / watched.progress.max) * 4
  } else if (watched.expectedPartials !== null && watched.expectedPartials > 0) {
    raw = (3 * watched.partialCount) / watched.expectedPartials
  }
  if (raw === null || !Number.isFinite(raw)) return null
  const tiles = clampTiles(raw)
  if (previousTiles === null) return tiles
  return Math.max(tiles, clampTiles(previousTiles)) as 0 | 1 | 2 | 3
}

/**
 * @param watched 生成画面で表示中の Run(無ければ null)。
 * @param queue App バーのバッジと同じキュー件数。
 * @param previousTiles 同じ runId で直前に表示した tiles(0〜3)。runId が変わったら null を渡す。
 */
export function deriveFaviconState(
  watched: WatchedRunInfo | null,
  queue: QueueCounts,
  previousTiles: number | null,
): FaviconState {
  if (watched === null) return fromQueueCounts(queue)

  if (watched.status === 'queued') return { kind: 'queued' }

  if (watched.status === 'running') {
    if (watched.connection === 'error') return { kind: 'spin' }
    const tiles = computeTiles(watched, previousTiles)
    return tiles === null ? { kind: 'spin' } : { kind: 'fill', tiles }
  }

  if (watched.observedActive) {
    if (watched.status === 'succeeded') return { kind: 'done' }
    if (watched.status === 'failed') return { kind: 'error' }
  }

  // canceled または不明(null): 見ている Run は終端になったので、キュー件数の規則に戻る。
  return fromQueueCounts(queue)
}

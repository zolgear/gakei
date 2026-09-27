/**
 * favicon と App バーのロゴの進捗表示を1箇所で束ねるフック(ADR-0009 8章)。`AppShell` で1回だけ
 * 呼ぶ。`useQueueStatus()`(App バーのバッジと同じ `['runs', 'queue-status']` クエリを共有するので
 * 追加のリクエストは増えない)と `watchedRunStore`(生成画面の `ResultPane` が書き込む「見ている
 * Run」)から `deriveFaviconState` の結果を求め、favicon の DOM コントローラ(`faviconController`)
 * へ渡す。App バーのロゴは `useFaviconIconState()` で、コントローラが今描いている図形(IconState)
 * を読む。
 * 設定「タブのアイコンで進捗を示す」(`faviconPrefs.ts`、2026-09-26 追加)は
 * `getController().setFaviconEnabled()` へそのまま渡す。オフでも IconState 自体は更新され続ける
 * ので、App バーのロゴの動きは設定に関係なく変わらない。
 */
import { useEffect, useSyncExternalStore } from 'react'
import { useQueueStatus } from '../../shell/useQueueStatus'
import { createFaviconController, type FaviconController } from './faviconController'
import { deriveFaviconState } from './deriveFaviconState'
import { getFaviconProgressEnabled, subscribeFaviconProgressEnabled } from './faviconPrefs'
import { getWatchedRun, subscribeWatchedRun } from './watchedRunStore'
import type { IconState } from './iconShape'

let controller: FaviconController | null = null

function getController(): FaviconController {
  if (!controller) controller = createFaviconController()
  return controller
}

// runId ごとの直近の tiles(単調増加のため。runId が変わったらリセットする)。
let lastRunId: string | null = null
let lastTiles: number | null = null

export function useFaviconProgress(): void {
  const queue = useQueueStatus()
  const watched = useSyncExternalStore(subscribeWatchedRun, getWatchedRun, getWatchedRun)
  const faviconProgressEnabled = useSyncExternalStore(
    subscribeFaviconProgressEnabled,
    getFaviconProgressEnabled,
    getFaviconProgressEnabled,
  )

  // AppShell がマウントされている間だけ favicon の <link> を差し替え、アンマウントで元に戻す。
  // dispose したコントローラは捨て、次のマウントでは作り直す(StrictMode の二重マウントでも
  // PNG の配色追従などが外れないように)。
  useEffect(() => {
    const ctrl = getController()
    return () => {
      ctrl.dispose()
      if (controller === ctrl) controller = null
    }
  }, [])

  // 設定が変わるたび(初回描画も含む)にコントローラへ反映する。
  useEffect(() => {
    getController().setFaviconEnabled(faviconProgressEnabled)
  }, [faviconProgressEnabled])

  useEffect(() => {
    if (watched?.runId !== lastRunId) {
      lastRunId = watched?.runId ?? null
      lastTiles = null
    }
    const state = deriveFaviconState(watched, { running: queue.running, queued: queue.queued }, lastTiles)
    if (state.kind === 'fill') lastTiles = state.tiles
    getController().setState(state)
  }, [watched, queue.running, queue.queued])
}

/** App バーのロゴが今描くべき IconState(favicon と同じ状態・同じ動き)。 */
export function useFaviconIconState(): IconState {
  return useSyncExternalStore(
    (listener) => getController().subscribe(listener),
    () => getController().getIconState(),
    () => getController().getIconState(),
  )
}

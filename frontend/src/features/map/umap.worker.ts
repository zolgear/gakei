/**
 * 地図の配置(umap-js)を UI のスレッドの外で計算する Web Worker(ADR-0033 8章)。
 * 途中経過の配置を送るので、画面では点が少しずつ動いて落ち着く。取り消しは呼び出し側が
 * worker ごと `terminate()` する。
 */
import { runUmapLayout } from './umapLayout'
import type { UmapWorkerRequest, UmapWorkerResponse } from './umapWorkerProtocol'

const ctx = self as unknown as {
  onmessage: ((event: MessageEvent<UmapWorkerRequest>) => void) | null
  postMessage: (message: UmapWorkerResponse, transfer: Transferable[]) => void
}

ctx.onmessage = (event) => {
  const { jobId, input, nEpochs, seed } = event.data
  const started = performance.now()
  try {
    const embedding = runUmapLayout(input, {
      nEpochs,
      seed,
      now: () => performance.now(),
      onProgress: (epoch, total, positions) => {
        ctx.postMessage({ type: 'progress', jobId, epoch, nEpochs: total, positions }, [positions.buffer])
      },
    })
    ctx.postMessage(
      { type: 'done', jobId, positions: embedding, elapsedMs: performance.now() - started },
      [embedding.buffer],
    )
  } catch (err) {
    ctx.postMessage({ type: 'error', jobId, message: err instanceof Error ? err.message : String(err) }, [])
  }
}

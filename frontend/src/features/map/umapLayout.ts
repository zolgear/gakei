/**
 * umap-js で 2D の配置を計算する(ADR-0033 8章、11章)。Web Worker(`umap.worker.ts`)から呼ぶ。
 * テストでは同じ関数を直接呼び、同じ入力なら同じ配置になることを確かめる。
 */
import { UMAP } from 'umap-js'
import { mulberry32 } from './random'
import type { UmapInput } from './umapInput'

/** 塊の中の点の最小の間隔(umap-js の minDist)。 */
export const UMAP_MIN_DIST = 0.5

export interface UmapLayoutOptions {
  nEpochs: number
  seed: number
  /** 途中経過を知らせる。`embedding` は x0, y0, x1, y1, ... の写し。 */
  onProgress?: (epoch: number, nEpochs: number, embedding: Float32Array) => void
  /** 途中経過を知らせる間隔(ミリ秒)。 */
  progressIntervalMs?: number
  now?: () => number
}

/** 計算して、最後の配置を x0, y0, x1, y1, ... で返す。 */
export function runUmapLayout(input: UmapInput, options: UmapLayoutOptions): Float32Array {
  const n = input.indices.length
  const umap = new UMAP({
    nComponents: 2,
    nNeighbors: input.nNeighbors,
    nEpochs: options.nEpochs,
    // 既定(0.1)より広げる。似た画像が1点に重なりすぎず、サムネイルを見分けやすい。
    minDist: UMAP_MIN_DIST,
    random: mulberry32(options.seed),
  })
  umap.setPrecomputedKNN(input.indices, input.distances)
  // 近傍を渡すと umap-js は X の長さだけを使う(要素は読まない)。
  const X = Array.from({ length: n }, () => [0])
  const nEpochs = umap.initializeFit(X)
  const now = options.now ?? (() => Date.now())
  const interval = options.progressIntervalMs ?? 80
  options.onProgress?.(0, nEpochs, flatten(umap.getEmbedding()))
  let last = now()
  for (let epoch = 1; epoch <= nEpochs; epoch++) {
    umap.step()
    if (options.onProgress && epoch < nEpochs && now() - last >= interval) {
      last = now()
      options.onProgress(epoch, nEpochs, flatten(umap.getEmbedding()))
    }
  }
  return flatten(umap.getEmbedding())
}

export function flatten(embedding: number[][]): Float32Array {
  const out = new Float32Array(embedding.length * 2)
  for (let i = 0; i < embedding.length; i++) {
    out[i * 2] = embedding[i][0]
    out[i * 2 + 1] = embedding[i][1]
  }
  return out
}

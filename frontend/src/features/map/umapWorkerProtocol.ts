/** 地図の配置の Web Worker とのやりとり。 */
import type { UmapInput } from './umapInput'

export interface UmapWorkerRequest {
  jobId: number
  input: UmapInput
  nEpochs: number
  seed: number
}

export type UmapWorkerResponse =
  | { type: 'progress'; jobId: number; epoch: number; nEpochs: number; positions: Float32Array }
  | { type: 'done'; jobId: number; positions: Float32Array; elapsedMs: number }
  | { type: 'error'; jobId: number; message: string }

/** 地図の配置の Web Worker とのやりとり。 */
import type { UmapInput } from './umapInput'

export interface UmapWorkerRequest {
  jobId: number
  input: UmapInput
  nEpochs: number
  seed: number
  /** 次元の数(2 か 3。省くと 2)。返る配置の刻み幅もこれになる。 */
  nComponents?: 2 | 3
}

export type UmapWorkerResponse =
  | { type: 'progress'; jobId: number; epoch: number; nEpochs: number; positions: Float32Array }
  | { type: 'done'; jobId: number; positions: Float32Array; elapsedMs: number }
  | { type: 'error'; jobId: number; message: string }

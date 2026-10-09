import { describe, expect, it } from 'vitest'
import {
  describeSdWebuiSeed,
  extractSdWebuiAllSeeds,
  extractSdWebuiInfotext,
  extractSdWebuiRequest,
  extractSdWebuiSeed,
  sdWebuiSeedTooltip,
  sdwebuiRequestDownloadFilename,
  usageWithoutInfotext,
} from './sdwebuiRunDisplay'
import { runSeedDisplay } from './runSeedDisplay'

describe('extract*', () => {
  it('sdwebui_seed と sdwebui_request を取り出す', () => {
    const params = { sdwebui_seed: 12, sdwebui_request: { prompt: 'x' }, sdwebui_task_id: 'gakei-r1' }
    expect(extractSdWebuiSeed(params)).toBe(12)
    expect(extractSdWebuiRequest(params)).toEqual({ prompt: 'x' })
    expect(extractSdWebuiSeed({ sdwebui_seed: '12' })).toBeNull()
    expect(extractSdWebuiRequest({ sdwebui_request: [1] })).toBeNull()
  })

  it('usage の all_seeds と infotext を取り出す', () => {
    expect(extractSdWebuiAllSeeds({ all_seeds: [1, 2] })).toEqual([1, 2])
    expect(extractSdWebuiAllSeeds({ all_seeds: [] })).toBeNull()
    expect(extractSdWebuiAllSeeds({ all_seeds: ['1'] })).toBeNull()
    expect(extractSdWebuiAllSeeds(null)).toBeNull()
    expect(extractSdWebuiInfotext({ infotext: 'a cat\nSteps: 20' })).toBe('a cat\nSteps: 20')
    expect(extractSdWebuiInfotext({ infotext: '  ' })).toBeNull()
  })
})

describe('describeSdWebuiSeed', () => {
  it('1枚なら seed だけ', () => {
    expect(describeSdWebuiSeed(42, [42], 1)).toBe('seed 42')
    expect(describeSdWebuiSeed(42, null, 0)).toBe('seed 42')
  })

  it('複数枚で特定の出力を見ているときは、その出力の seed と何枚目か', () => {
    expect(describeSdWebuiSeed(42, [42, 43, 44], 3, 1)).toBe('seed 43(3 枚中 2 枚目)')
  })

  it('複数枚で Run 全体なら、1枚ごとの seed を並べる', () => {
    expect(describeSdWebuiSeed(42, [42, 43], 2)).toBe('seed 42, 43(1枚ごとに別の seed)')
  })

  it('all_seeds が無ければ sdwebui_seed だけ', () => {
    expect(describeSdWebuiSeed(42, null, 2, 1)).toBe('seed 42')
    expect(describeSdWebuiSeed(null, null, 2)).toBeNull()
  })

  it('ツールチップは複数枚のときだけ', () => {
    expect(sdWebuiSeedTooltip(1)).toBeUndefined()
    expect(sdWebuiSeedTooltip(2)).toBeTruthy()
  })
})

describe('runSeedDisplay', () => {
  it('SD WebUI の Run は usage.all_seeds を使う', () => {
    const run = { params: { sdwebui_seed: 5 }, usage: { all_seeds: [5, 6] }, outputs: [{}, {}] }
    expect(runSeedDisplay(run, 1)?.text).toBe('seed 6(2 枚中 2 枚目)')
  })

  it('ComfyUI の Run は従来どおり', () => {
    const run = { params: { comfyui_seed: 5 }, outputs: [{}, {}, {}] }
    expect(runSeedDisplay(run, 1)?.text).toBe('seed 5 ・ 3 枚中 2 枚目')
  })

  it('どちらでもなければ null', () => {
    expect(runSeedDisplay({ params: { seed: 1 }, outputs: [{}] })).toBeNull()
  })
})

describe('その他', () => {
  it('ファイル名', () => {
    expect(sdwebuiRequestDownloadFilename('r1')).toBe('sdwebui-request-r1.json')
  })

  it('usage から infotext だけを除く', () => {
    expect(usageWithoutInfotext({ infotext: 'x', all_seeds: [1], duration_ms: 3 })).toEqual({
      all_seeds: [1],
      duration_ms: 3,
    })
    const u = { duration_ms: 3 }
    expect(usageWithoutInfotext(u)).toBe(u)
  })
})

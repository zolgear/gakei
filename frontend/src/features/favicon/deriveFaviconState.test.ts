import { describe, expect, it } from 'vitest'
import { deriveFaviconState } from './deriveFaviconState'
import type { WatchedRunInfo } from './watchedRunStore'

const NO_QUEUE = { running: 0, queued: 0 }

function watched(partial: Partial<WatchedRunInfo>): WatchedRunInfo {
  return {
    runId: 'run-1',
    status: 'running',
    connection: 'open',
    progress: null,
    partialCount: 0,
    expectedPartials: null,
    observedActive: true,
    ...partial,
  }
}

describe('deriveFaviconState: 見ている Run が無いとき', () => {
  it('running>0 なら spin', () => {
    expect(deriveFaviconState(null, { running: 1, queued: 0 }, null)).toEqual({ kind: 'spin' })
  })

  it('running=0・queued>0 なら queued', () => {
    expect(deriveFaviconState(null, { running: 0, queued: 2 }, null)).toEqual({ kind: 'queued' })
  })

  it('どちらも0なら idle', () => {
    expect(deriveFaviconState(null, NO_QUEUE, null)).toEqual({ kind: 'idle' })
  })
})

describe('deriveFaviconState: 見ている Run があるとき', () => {
  it('status=queued は queued(キュー件数によらない)', () => {
    expect(deriveFaviconState(watched({ status: 'queued' }), NO_QUEUE, null)).toEqual({ kind: 'queued' })
  })

  it('running かつ connection=error は進捗不明として spin', () => {
    const w = watched({ status: 'running', connection: 'error', progress: { value: 5, max: 10 } })
    expect(deriveFaviconState(w, NO_QUEUE, null)).toEqual({ kind: 'spin' })
  })

  it('running で progress があれば value/max×4 を3で頭打ちにした tiles', () => {
    expect(deriveFaviconState(watched({ progress: { value: 1, max: 10 } }), NO_QUEUE, null)).toEqual({
      kind: 'fill',
      tiles: 0,
    })
    expect(deriveFaviconState(watched({ progress: { value: 5, max: 10 } }), NO_QUEUE, null)).toEqual({
      kind: 'fill',
      tiles: 2,
    })
    expect(deriveFaviconState(watched({ progress: { value: 10, max: 10 } }), NO_QUEUE, null)).toEqual({
      kind: 'fill',
      tiles: 3, // 4マス目(満杯)は成功時にだけ点ける
    })
  })

  it('progress の max が0以下は0除算を避けて使えない扱いにする', () => {
    const w = watched({ progress: { value: 5, max: 0 }, expectedPartials: null })
    expect(deriveFaviconState(w, NO_QUEUE, null)).toEqual({ kind: 'spin' })
  })

  it('progress が無く expectedPartials があれば 3×partialCount/expectedPartials', () => {
    const w = watched({ progress: null, partialCount: 1, expectedPartials: 4 })
    expect(deriveFaviconState(w, NO_QUEUE, null)).toEqual({ kind: 'fill', tiles: 0 })
    const w2 = watched({ progress: null, partialCount: 2, expectedPartials: 4 })
    expect(deriveFaviconState(w2, NO_QUEUE, null)).toEqual({ kind: 'fill', tiles: 1 })
    const w3 = watched({ progress: null, partialCount: 4, expectedPartials: 4 })
    expect(deriveFaviconState(w3, NO_QUEUE, null)).toEqual({ kind: 'fill', tiles: 3 })
  })

  it('progress も expectedPartials も無ければ spin(進捗不明)', () => {
    expect(deriveFaviconState(watched({ progress: null, expectedPartials: null }), NO_QUEUE, null)).toEqual({
      kind: 'spin',
    })
  })

  it('expectedPartials が null/0 のときも spin(0除算を避ける)', () => {
    expect(deriveFaviconState(watched({ progress: null, expectedPartials: 0, partialCount: 0 }), NO_QUEUE, null)).toEqual({
      kind: 'spin',
    })
  })

  it('succeeded は done、failed は error', () => {
    expect(deriveFaviconState(watched({ status: 'succeeded' }), NO_QUEUE, null)).toEqual({ kind: 'done' })
    expect(deriveFaviconState(watched({ status: 'failed' }), NO_QUEUE, null)).toEqual({ kind: 'error' })
  })

  it('canceled は他に active な Run が無ければ idle', () => {
    expect(deriveFaviconState(watched({ status: 'canceled' }), NO_QUEUE, null)).toEqual({ kind: 'idle' })
  })

  it('canceled でも他に running があれば spin、queued があれば queued(キュー件数の規則に戻る)', () => {
    expect(deriveFaviconState(watched({ status: 'canceled' }), { running: 1, queued: 0 }, null)).toEqual({
      kind: 'spin',
    })
    expect(deriveFaviconState(watched({ status: 'canceled' }), { running: 0, queued: 3 }, null)).toEqual({
      kind: 'queued',
    })
  })

  it('status が不明(null)でも canceled と同じくキュー件数の規則に戻る', () => {
    expect(deriveFaviconState(watched({ status: null }), { running: 0, queued: 1 }, null)).toEqual({
      kind: 'queued',
    })
  })
})

describe('deriveFaviconState: 単調増加(tiles は減らない)', () => {
  it('前回より小さい計算結果でも、前回の tiles を下回らない', () => {
    const w = watched({ progress: { value: 1, max: 10 } }) // 生の計算では tiles=0
    expect(deriveFaviconState(w, NO_QUEUE, 2)).toEqual({ kind: 'fill', tiles: 2 })
  })

  it('計算結果が前回より大きければ、それを採用する', () => {
    const w = watched({ progress: { value: 9, max: 10 } }) // tiles=3
    expect(deriveFaviconState(w, NO_QUEUE, 1)).toEqual({ kind: 'fill', tiles: 3 })
  })

  it('previousTiles が null(runId が変わった直後)なら計算結果をそのまま使う', () => {
    const w = watched({ progress: { value: 1, max: 10 } })
    expect(deriveFaviconState(w, NO_QUEUE, null)).toEqual({ kind: 'fill', tiles: 0 })
  })
})

describe('deriveFaviconState: 完了済みの Run を開き直したとき(observedActive=false)', () => {
  it('succeeded でも done の演出は出さず、キュー件数の規則に戻る', () => {
    const w = watched({ status: 'succeeded', observedActive: false })
    expect(deriveFaviconState(w, NO_QUEUE, null)).toEqual({ kind: 'idle' })
    expect(deriveFaviconState(w, { running: 1, queued: 0 }, null)).toEqual({ kind: 'spin' })
  })

  it('failed でも error は出さない', () => {
    const w = watched({ status: 'failed', observedActive: false })
    expect(deriveFaviconState(w, NO_QUEUE, null)).toEqual({ kind: 'idle' })
  })

  it('実行中を見ていた Run なら succeeded → done、failed → error', () => {
    expect(deriveFaviconState(watched({ status: 'succeeded' }), { running: 1, queued: 0 }, null)).toEqual({
      kind: 'done',
    })
    expect(deriveFaviconState(watched({ status: 'failed' }), { running: 1, queued: 0 }, null)).toEqual({
      kind: 'error',
    })
  })
})

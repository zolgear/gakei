import { describe, expect, it } from 'vitest'
import {
  closeLineageView,
  lineageNodeTarget,
  openLineageView,
  publicShareHistoryState,
  publicShareViewFromLocation,
  publicShareViewPath,
} from './publicShareView'

const RUN_ID = '0b9a4a3e-6a53-4a6f-9d0e-3f1c2b7d8e90'
const ASSET_ID = '7c1f0d2e-1111-4a6f-9d0e-3f1c2b7d8e90'

describe('publicShareViewPath / publicShareHistoryState', () => {
  it('ビューア・Run の詳細・全画面の系列グラフの URL を組み立てる', () => {
    expect(publicShareViewPath('abc', { runId: null, lineage: false })).toBe('/s/abc')
    expect(publicShareViewPath('abc', { runId: RUN_ID, lineage: false })).toBe(`/s/abc/runs/${RUN_ID}`)
    // 全画面では、開いていた Run を URL に書かない。
    expect(publicShareViewPath('abc', { runId: RUN_ID, lineage: true })).toBe('/s/abc/lineage')
  })

  it('全画面のときだけ、戻る Run を履歴の state に持たせる', () => {
    expect(publicShareHistoryState({ runId: RUN_ID, lineage: false })).toBeNull()
    expect(publicShareHistoryState({ runId: RUN_ID, lineage: true })).toEqual({ gakeiShareReturnRunId: RUN_ID })
  })
})

describe('publicShareViewFromLocation', () => {
  it('URL から開いているものを決める', () => {
    expect(publicShareViewFromLocation('/s/abc', null)).toEqual({ runId: null, lineage: false })
    expect(publicShareViewFromLocation(`/s/abc/runs/${RUN_ID}`, null)).toEqual({ runId: RUN_ID, lineage: false })
    expect(publicShareViewFromLocation('/settings', null)).toBeNull()
  })

  it('全画面の直リンク(state なし)は、戻る Run を持たない', () => {
    expect(publicShareViewFromLocation('/s/abc/lineage', null)).toEqual({ runId: null, lineage: true })
    expect(publicShareViewFromLocation('/s/abc/lineage', { other: 1 })).toEqual({ runId: null, lineage: true })
  })

  it('全画面の履歴の state から、戻る Run を復元する(Run 以外の URL では state を見ない)', () => {
    const state = publicShareHistoryState({ runId: RUN_ID, lineage: true })
    expect(publicShareViewFromLocation('/s/abc/lineage', state)).toEqual({ runId: RUN_ID, lineage: true })
    expect(publicShareViewFromLocation('/s/abc', state)).toEqual({ runId: null, lineage: false })
  })
})

describe('openLineageView / closeLineageView', () => {
  it('Run の詳細から広げて閉じると、その Run の詳細に戻る', () => {
    const opened = openLineageView({ runId: RUN_ID, lineage: false })
    expect(opened).toEqual({ runId: RUN_ID, lineage: true })
    expect(closeLineageView(opened)).toEqual({ runId: RUN_ID, lineage: false })
  })

  it('画像の情報から広げて閉じると、画像の情報に戻る', () => {
    expect(closeLineageView(openLineageView({ runId: null, lineage: false }))).toEqual({
      runId: null,
      lineage: false,
    })
  })
})

describe('lineageNodeTarget', () => {
  it('画像のノードは全画面を閉じ、ビューアでその画像を開く(画像の情報に戻る)', () => {
    expect(lineageNodeTarget({ id: ASSET_ID, type: 'asset' })).toEqual({
      view: { runId: null, lineage: false },
      assetId: ASSET_ID,
    })
  })

  it('Generated のノードは全画面を閉じ、その Run の詳細を開く', () => {
    const target = lineageNodeTarget({ id: RUN_ID, type: 'run' })
    expect(target).toEqual({ view: { runId: RUN_ID, lineage: false }, assetId: null })
    expect(publicShareViewPath('abc', target!.view)).toBe(`/s/abc/runs/${RUN_ID}`)
  })

  it('種類の分からないノードは何もしない', () => {
    expect(lineageNodeTarget({ id: 'x', type: undefined })).toBeNull()
  })
})

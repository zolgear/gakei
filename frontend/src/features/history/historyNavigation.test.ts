import { describe, expect, it } from 'vitest'
import { resolveHistoryCardNavigation } from './historyNavigation'

const RUN_ID = '11111111-1111-1111-1111-111111111111'
const OUTPUT_ID = '22222222-2222-2222-2222-222222222222'
const PARENT_ID = '33333333-3333-3333-3333-333333333333'

describe('resolveHistoryCardNavigation', () => {
  it('プロンプト部分のクリック先は常に /runs/{id}(状態や入出力の有無によらない)', () => {
    expect(
      resolveHistoryCardNavigation({
        runId: RUN_ID,
        status: 'failed',
        firstOutputAssetId: null,
        primaryParentAssetId: null,
      }).runHref,
    ).toBe(`/runs/${RUN_ID}`)

    expect(
      resolveHistoryCardNavigation({
        runId: RUN_ID,
        status: 'succeeded',
        firstOutputAssetId: OUTPUT_ID,
        primaryParentAssetId: PARENT_ID,
      }).runHref,
    ).toBe(`/runs/${RUN_ID}`)
  })

  it('画像領域のクリック先: 出力があればその Asset ビューアへ直接遷移する(実行中・待機中でも出力があれば優先)', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'succeeded',
      firstOutputAssetId: OUTPUT_ID,
      primaryParentAssetId: null,
    })
    expect(nav.mediaHref).toBe(`/assets/${OUTPUT_ID}`)
  })

  it('画像領域のクリック先: 出力が無く終わっている(failed)場合は Run 詳細のまま', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'failed',
      firstOutputAssetId: null,
      primaryParentAssetId: PARENT_ID,
    })
    expect(nav.mediaHref).toBe(`/runs/${RUN_ID}`)
  })

  it('画像領域のクリック先: 出力・入力とも無い(入力なし失敗)場合も Run 詳細', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'failed',
      firstOutputAssetId: null,
      primaryParentAssetId: null,
    })
    expect(nav.mediaHref).toBe(`/runs/${RUN_ID}`)
  })

  it('画像領域のクリック先: 出力・入力とも無い(canceled)場合も Run 詳細', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'canceled',
      firstOutputAssetId: null,
      primaryParentAssetId: null,
    })
    expect(nav.mediaHref).toBe(`/runs/${RUN_ID}`)
  })

  it('画像領域のクリック先: 実行中(running)で出力が無ければスタジオの生成画面(?run=)へ', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'running',
      firstOutputAssetId: null,
      primaryParentAssetId: PARENT_ID,
    })
    expect(nav.mediaHref).toBe(`/studio?run=${RUN_ID}`)
  })

  it('画像領域のクリック先: 待機中(queued)で出力が無ければスタジオの生成画面(?run=)へ', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'queued',
      firstOutputAssetId: null,
      primaryParentAssetId: null,
    })
    expect(nav.mediaHref).toBe(`/studio?run=${RUN_ID}`)
  })

  it('succeeded(出力あり): 系列ボタンを出し、先頭の出力を起点にする', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'succeeded',
      firstOutputAssetId: OUTPUT_ID,
      primaryParentAssetId: null,
    })
    expect(nav.showLineageButton).toBe(true)
    expect(nav.lineageOriginAssetId).toBe(OUTPUT_ID)
  })

  it('failed かつ入力あり(出力は無い): 系列ボタンを出し、主たる親を起点にする', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'failed',
      firstOutputAssetId: null,
      primaryParentAssetId: PARENT_ID,
    })
    expect(nav.showLineageButton).toBe(true)
    expect(nav.lineageOriginAssetId).toBe(PARENT_ID)
  })

  it('failed かつ入力なし(出力も無い、Generate の失敗): 系列ボタンを出さない', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'failed',
      firstOutputAssetId: null,
      primaryParentAssetId: null,
    })
    expect(nav.showLineageButton).toBe(false)
    expect(nav.lineageOriginAssetId).toBeNull()
  })

  it('queued/running(出力も親も無い想定): 系列ボタンを出さない', () => {
    const navQueued = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'queued',
      firstOutputAssetId: null,
      primaryParentAssetId: null,
    })
    expect(navQueued.showLineageButton).toBe(false)

    const navRunning = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'running',
      firstOutputAssetId: null,
      primaryParentAssetId: null,
    })
    expect(navRunning.showLineageButton).toBe(false)
  })

  it('出力と入力(親)の両方がある場合は出力を優先する', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'succeeded',
      firstOutputAssetId: OUTPUT_ID,
      primaryParentAssetId: PARENT_ID,
    })
    expect(nav.lineageOriginAssetId).toBe(OUTPUT_ID)
  })

  it('canceled で出力・親どちらも無ければ系列ボタンを出さない', () => {
    const nav = resolveHistoryCardNavigation({
      runId: RUN_ID,
      status: 'canceled',
      firstOutputAssetId: null,
      primaryParentAssetId: null,
    })
    expect(nav.showLineageButton).toBe(false)
  })
})

import { describe, expect, it } from 'vitest'
import { lineageExportQuery } from '../../api/client'
import {
  LINEAGE_EXPORT_MODES,
  LINEAGE_EXPORT_SCOPES,
  MAX_LINEAGE_ZIP_BYTES,
  checkLineageZipFile,
  exportPreviewGraphSummary,
  importNavigationState,
  importToastFromState,
  progressPercent,
} from './lineageTransfer'

describe('checkLineageZipFile', () => {
  it('拡張子 .zip(大文字も)なら ok', () => {
    expect(checkLineageZipFile({ name: 'a.zip', type: '', size: 10 })).toBe('ok')
    expect(checkLineageZipFile({ name: 'A.ZIP', type: '', size: 10 })).toBe('ok')
  })

  it('拡張子が無くても ZIP の MIME なら ok', () => {
    expect(checkLineageZipFile({ name: 'lineage', type: 'application/zip', size: 10 })).toBe('ok')
    expect(checkLineageZipFile({ name: 'lineage', type: 'application/x-zip-compressed', size: 10 })).toBe('ok')
  })

  it('画像などは notZip', () => {
    expect(checkLineageZipFile({ name: 'a.png', type: 'image/png', size: 10 })).toBe('notZip')
  })

  it('空と上限超え', () => {
    expect(checkLineageZipFile({ name: 'a.zip', type: '', size: 0 })).toBe('empty')
    expect(checkLineageZipFile({ name: 'a.zip', type: '', size: MAX_LINEAGE_ZIP_BYTES })).toBe('ok')
    expect(checkLineageZipFile({ name: 'a.zip', type: '', size: MAX_LINEAGE_ZIP_BYTES + 1 })).toBe('tooLarge')
  })
})

describe('progressPercent', () => {
  it('0〜1 を 0〜100 の整数にする', () => {
    expect(progressPercent(0)).toBe(0)
    expect(progressPercent(0.256)).toBe(25)
    expect(progressPercent(0.999)).toBe(99)
    expect(progressPercent(1)).toBe(100)
  })

  it('範囲外と NaN は端に寄せる', () => {
    expect(progressPercent(-1)).toBe(0)
    expect(progressPercent(2)).toBe(100)
    expect(progressPercent(Number.NaN)).toBe(0)
  })
})

describe('LINEAGE_EXPORT_SCOPES', () => {
  it('「この画像と祖先」「この画像と子孫」「系列全体」の順', () => {
    expect(LINEAGE_EXPORT_SCOPES).toEqual(['ancestors', 'descendants', 'lineage'])
  })
})

describe('importToastFromState', () => {
  it('importNavigationState で作った state から文言を取り出す', () => {
    expect(importToastFromState(importNavigationState('取り込みました'))).toBe('取り込みました')
  })

  it('state が無い・形が違うときは null', () => {
    expect(importToastFromState(null)).toBeNull()
    expect(importToastFromState(undefined)).toBeNull()
    expect(importToastFromState('x')).toBeNull()
    expect(importToastFromState({ other: 1 })).toBeNull()
    expect(importToastFromState({ lineageImportToast: '' })).toBeNull()
    expect(importToastFromState({ lineageImportToast: 3 })).toBeNull()
  })
})

describe('LINEAGE_EXPORT_MODES', () => {
  it('「GAKEI に取り込む」「納品用」の順', () => {
    expect(LINEAGE_EXPORT_MODES).toEqual(['import', 'delivery'])
  })
})

describe('lineageExportQuery', () => {
  it('取り込み用は範囲と用途だけ(名前は既定で含めない)', () => {
    const query = lineageExportQuery({
      scope: 'ancestors',
      mode: 'import',
      includeCreatorNames: false,
      lang: 'en',
      timeZone: 'Asia/Tokyo',
    })
    expect(query.toString()).toBe('scope=ancestors&mode=import')
  })

  it('納品用は言語とタイムゾーンを送り、選んだときだけ名前を含める', () => {
    const query = lineageExportQuery({
      scope: 'lineage',
      mode: 'delivery',
      includeCreatorNames: true,
      lang: 'ja',
      timeZone: 'Asia/Tokyo',
    })
    expect(query.get('scope')).toBe('lineage')
    expect(query.get('mode')).toBe('delivery')
    expect(query.get('include_creator_names')).toBe('true')
    expect(query.get('lang')).toBe('ja')
    expect(query.get('tz')).toBe('Asia/Tokyo')
  })

  it('タイムゾーンが分からなければ省く', () => {
    const query = lineageExportQuery({
      scope: 'ancestors',
      mode: 'delivery',
      includeCreatorNames: false,
      lang: 'en',
    })
    expect(query.has('tz')).toBe(false)
    expect(query.has('include_creator_names')).toBe(false)
  })
})

describe('exportPreviewGraphSummary', () => {
  const node = (id: string, type: 'asset' | 'run') => ({
    id,
    type,
    depth: 0,
    deleted: false,
    embedded: false,
    local_hidden: false,
  })

  it('グラフが無ければ null', () => {
    expect(exportPreviewGraphSummary(undefined)).toBeNull()
    expect(
      exportPreviewGraphSummary({
        scope: 'ancestors',
        asset_count: 1,
        run_count: 0,
        total_bytes: 1,
        truncated: false,
        omitted_input_count: 0,
      }),
    ).toBeNull()
  })

  it('ノードの種類ごとに数え、範囲の外の入力と打ち切りを返す', () => {
    expect(
      exportPreviewGraphSummary({
        scope: 'descendants',
        asset_count: 2,
        run_count: 1,
        total_bytes: 10,
        omitted_input_count: 1,
        truncated: false,
        graph: {
          root_asset_id: 'a',
          nodes: [node('a', 'asset'), node('r', 'run'), node('b', 'asset')],
          edges: [],
          truncated: true,
        },
      }),
    ).toEqual({ assetCount: 2, runCount: 1, omittedInputCount: 1, truncated: true })
  })
})

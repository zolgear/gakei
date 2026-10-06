import { describe, expect, it } from 'vitest'
import {
  LINEAGE_EXPORT_SCOPES,
  MAX_LINEAGE_ZIP_BYTES,
  checkLineageZipFile,
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
  it('「この画像と祖先」「系列全体」の順', () => {
    expect(LINEAGE_EXPORT_SCOPES).toEqual(['ancestors', 'lineage'])
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

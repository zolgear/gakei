import { describe, expect, it } from 'vitest'
import { DERIVED_VERSION, assetUrl, publicShareAssetUrl } from './assetUrl'

const ASSET_ID = '3fa85f64-5717-4562-b3fc-2c963f66afa6'

describe('assetUrl', () => {
  it('派生(thumb / preview)には版(dv)を付ける(ADR-0036 3章)', () => {
    expect(assetUrl(ASSET_ID, 'thumb')).toBe(
      `/api/assets/${ASSET_ID}/content?variant=thumb&dv=${DERIVED_VERSION}`,
    )
    expect(assetUrl(ASSET_ID, 'preview')).toBe(
      `/api/assets/${ASSET_ID}/content?variant=preview&dv=${DERIVED_VERSION}`,
    )
  })

  it('原本には版を付けない', () => {
    expect(assetUrl(ASSET_ID, 'original')).toBe(`/api/assets/${ASSET_ID}/content?variant=original`)
    expect(assetUrl(ASSET_ID, 'original', { download: true })).toBe(
      `/api/assets/${ASSET_ID}/content?variant=original&download=1`,
    )
  })

  it('ダウンロードの指定は派生でも残す', () => {
    expect(assetUrl(ASSET_ID, 'thumb', { download: true })).toBe(
      `/api/assets/${ASSET_ID}/content?variant=thumb&dv=${DERIVED_VERSION}&download=1`,
    )
  })
})

describe('publicShareAssetUrl', () => {
  it('共有リンクの派生にも版を付け、原本には付けない', () => {
    expect(publicShareAssetUrl('to/ken', ASSET_ID, 'thumb')).toBe(
      `/api/public/shares/to%2Fken/assets/${ASSET_ID}/content?variant=thumb&dv=${DERIVED_VERSION}`,
    )
    expect(publicShareAssetUrl('tok', ASSET_ID, 'original', { download: true })).toBe(
      `/api/public/shares/tok/assets/${ASSET_ID}/content?variant=original&download=1`,
    )
  })
})

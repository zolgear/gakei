import { describe, expect, it } from 'vitest'
import {
  GAKEI_ASSET_ID_DATA_TYPE,
  extractAssetIdFromContentUrl,
  filterImageFiles,
  interpretDroppedData,
  isRelevantDragTypes,
} from './dragDropAssets'

const ASSET_ID = '3fa85f64-5717-4562-b3fc-2c963f66afa6'

describe('extractAssetIdFromContentUrl', () => {
  it('相対 URL から Asset ID を取り出す', () => {
    expect(extractAssetIdFromContentUrl(`/api/assets/${ASSET_ID}/content?variant=thumb`)).toBe(
      ASSET_ID,
    )
  })

  it('絶対 URL(別オリジン表記含む)からも取り出す', () => {
    expect(
      extractAssetIdFromContentUrl(`http://localhost:5173/api/assets/${ASSET_ID}/content?variant=thumb`),
    ).toBe(ASSET_ID)
  })

  it('クエリなしの URL でも取り出す', () => {
    expect(extractAssetIdFromContentUrl(`/api/assets/${ASSET_ID}/content`)).toBe(ASSET_ID)
  })

  it('複数行(コメント行あり)の uri-list から最初の有効な行を見る', () => {
    const uriList = `# comment\n/api/assets/${ASSET_ID}/content?variant=thumb\n`
    expect(extractAssetIdFromContentUrl(uriList)).toBe(ASSET_ID)
  })

  it('自アプリの Asset URL でなければ null', () => {
    expect(extractAssetIdFromContentUrl('https://example.com/some/image.webp')).toBeNull()
  })

  it('空文字なら null', () => {
    expect(extractAssetIdFromContentUrl('')).toBeNull()
  })
})

describe('filterImageFiles', () => {
  it('image/* だけを残す', () => {
    const files = [{ type: 'image/webp' }, { type: 'text/uri-list' }, { type: 'image/png' }]
    expect(filterImageFiles(files)).toEqual([{ type: 'image/webp' }, { type: 'image/png' }])
  })

  it('image/* が無ければ空配列', () => {
    expect(filterImageFiles([{ type: 'text/plain' }])).toEqual([])
  })
})

describe('interpretDroppedData', () => {
  it('GAKEI_ASSET_ID_DATA_TYPE があれば asset として扱う(files があっても無視)', () => {
    const result = interpretDroppedData({
      assetIdData: ASSET_ID,
      uriListData: null,
      files: [{ type: 'image/webp' }],
    })
    expect(result).toEqual({ kind: 'asset', assetId: ASSET_ID })
  })

  it('マーカーが無くても自アプリの text/uri-list なら asset として扱う', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: `/api/assets/${ASSET_ID}/content?variant=thumb`,
      files: [{ type: 'image/webp' }],
    })
    expect(result).toEqual({ kind: 'asset', assetId: ASSET_ID })
  })

  it('マーカーも自アプリ URL も無ければ、image/* のファイルとして扱う', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: null,
      files: [{ type: 'image/png' }, { type: 'text/plain' }],
    })
    expect(result).toEqual({ kind: 'files', files: [{ type: 'image/png' }] })
  })

  it('外部の text/uri-list(自アプリの Asset URL でない)は無視してファイル扱いにする', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: 'https://example.com/photo.png',
      files: [{ type: 'image/png' }],
    })
    expect(result).toEqual({ kind: 'files', files: [{ type: 'image/png' }] })
  })

  it('image/* が1件も無ければ none', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: null,
      files: [{ type: 'text/plain' }],
    })
    expect(result).toEqual({ kind: 'none' })
  })

  it('files が空でも none', () => {
    const result = interpretDroppedData({ assetIdData: null, uriListData: null, files: [] })
    expect(result).toEqual({ kind: 'none' })
  })

  it('空文字は null 扱いと同じ(マーカーなし)', () => {
    const result = interpretDroppedData({
      assetIdData: '',
      uriListData: '',
      files: [{ type: 'image/png' }],
    })
    expect(result).toEqual({ kind: 'files', files: [{ type: 'image/png' }] })
  })

  it('GAKEI_ASSET_ID_DATA_TYPE の値そのもの', () => {
    expect(GAKEI_ASSET_ID_DATA_TYPE).toBe('application/x-gakei-asset-id')
  })
})

describe('isRelevantDragTypes', () => {
  it('Files を含めば true', () => {
    expect(isRelevantDragTypes(['Files'])).toBe(true)
  })

  it('アプリ内 Asset マーカーを含めば true', () => {
    expect(isRelevantDragTypes([GAKEI_ASSET_ID_DATA_TYPE])).toBe(true)
  })

  it('text/uri-list を含めば true', () => {
    expect(isRelevantDragTypes(['text/uri-list'])).toBe(true)
  })

  it('text/plain だけなら false(通常のテキストドラッグを奪わない)', () => {
    expect(isRelevantDragTypes(['text/plain'])).toBe(false)
  })

  it('空なら false', () => {
    expect(isRelevantDragTypes([])).toBe(false)
  })
})

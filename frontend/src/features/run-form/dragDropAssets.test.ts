import { describe, expect, it } from 'vitest'
import { assetUrl } from '../../api/assetUrl'
import {
  GAKEI_ASSET_ID_DATA_TYPE,
  dragMayCarryImage,
  extractAssetIdFromContentUrl,
  extractAssetIdFromHtml,
  filterImageFiles,
  interpretDroppedData,
  isRelevantDragTypes,
  setAssetDragData,
} from './dragDropAssets'

const ASSET_ID = '3fa85f64-5717-4562-b3fc-2c963f66afa6'

describe('extractAssetIdFromContentUrl', () => {
  it('相対 URL から Asset ID を取り出す', () => {
    expect(extractAssetIdFromContentUrl(`/api/assets/${ASSET_ID}/content?variant=thumb`)).toBe(
      ASSET_ID,
    )
  })

  it('絶対 URL は同じオリジンのときだけ取り出す', () => {
    const url = `http://localhost:5173/api/assets/${ASSET_ID}/content?variant=thumb`
    expect(extractAssetIdFromContentUrl(url, 'http://localhost:5173')).toBe(ASSET_ID)
    expect(extractAssetIdFromContentUrl(url, 'http://localhost:8000')).toBeNull()
    expect(extractAssetIdFromContentUrl(url, 'https://localhost:5173')).toBeNull()
    // オリジンが分からなければ絶対 URL は信じない。
    expect(extractAssetIdFromContentUrl(url)).toBeNull()
  })

  it('プロトコル相対(//host/...)は相対 URL として扱わない', () => {
    expect(extractAssetIdFromContentUrl(`//evil.example/api/assets/${ASSET_ID}/content`)).toBeNull()
    expect(
      extractAssetIdFromContentUrl(`//evil.example/api/assets/${ASSET_ID}/content`, 'http://localhost:5173'),
    ).toBeNull()
  })

  it('配信 URL 以外のパス(ビューアのページ、別の API)は null', () => {
    expect(extractAssetIdFromContentUrl(`/assets/${ASSET_ID}`)).toBeNull()
    expect(extractAssetIdFromContentUrl(`/api/assets/${ASSET_ID}`)).toBeNull()
    expect(extractAssetIdFromContentUrl(`/x/api/assets/${ASSET_ID}/content`)).toBeNull()
  })

  it('派生の版(dv)付きの URL(assetUrl が作るもの)からも取り出す(ADR-0036)', () => {
    expect(extractAssetIdFromContentUrl(`/api/assets/${ASSET_ID}/content?variant=thumb&dv=1`)).toBe(
      ASSET_ID,
    )
    expect(extractAssetIdFromContentUrl(assetUrl(ASSET_ID, 'preview'))).toBe(ASSET_ID)
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

describe('extractAssetIdFromHtml', () => {
  const ORIGIN = 'http://127.0.0.1:8000'

  it('img の src(相対・&amp; 入りの問い合わせ)から取り出す', () => {
    expect(
      extractAssetIdFromHtml(`<meta charset="utf-8"><img src="/api/assets/${ASSET_ID}/content?variant=thumb&amp;dv=1" alt="">`),
    ).toBe(ASSET_ID)
  })

  it('リンクで包まれた img(マップの選んだ画像のカード)からも取り出す', () => {
    const html = `<a href="${ORIGIN}/assets/${ASSET_ID}"><img class="x" src="${ORIGIN}/api/assets/${ASSET_ID}/content?variant=thumb&amp;dv=1"></a>`
    expect(extractAssetIdFromHtml(html, ORIGIN)).toBe(ASSET_ID)
  })

  it('別のオリジンの img は null', () => {
    expect(extractAssetIdFromHtml(`<img src="https://other.example/api/assets/${ASSET_ID}/content">`, ORIGIN)).toBeNull()
  })

  it('img が無ければ null', () => {
    expect(extractAssetIdFromHtml('<p>hello</p>', ORIGIN)).toBeNull()
  })
})

describe('interpretDroppedData(ページの中か外か)', () => {
  const ORIGIN = 'http://127.0.0.1:8000'
  const contentUrl = `${ORIGIN}/api/assets/${ASSET_ID}/content?variant=card&dv=1`

  it('印の無いページ内の img: 同じオリジンの uri-list なら Asset として使う(File は送らない)', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: contentUrl,
      files: [{ type: 'image/webp' }],
      fromPage: true,
      origin: ORIGIN,
    })
    expect(result).toEqual({ kind: 'asset', assetId: ASSET_ID })
  })

  it('印の無いページ内のリンク付き img: uri-list がリンクでも html の img から Asset を使う', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: `${ORIGIN}/assets/${ASSET_ID}`,
      htmlData: `<a href="${ORIGIN}/assets/${ASSET_ID}"><img src="${contentUrl.replace('&', '&amp;')}"></a>`,
      files: [{ type: 'image/webp' }],
      fromPage: true,
      origin: ORIGIN,
    })
    expect(result).toEqual({ kind: 'asset', assetId: ASSET_ID })
  })

  it('読み取れないページ内のドラッグは none(重複を作らない)', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: 'https://example.com/photo.png',
      htmlData: '<img src="https://example.com/photo.png">',
      files: [{ type: 'image/png' }],
      fromPage: true,
      origin: ORIGIN,
    })
    expect(result).toEqual({ kind: 'none' })
  })

  it('ページ内でも別のオリジンの Asset URL は信じない', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: `https://other.example/api/assets/${ASSET_ID}/content`,
      files: [{ type: 'image/png' }],
      fromPage: true,
      origin: ORIGIN,
    })
    expect(result).toEqual({ kind: 'none' })
  })

  it('外からのファイルは今までどおりファイルとして扱う', () => {
    const result = interpretDroppedData({
      assetIdData: null,
      uriListData: null,
      htmlData: null,
      files: [{ type: 'image/png' }],
      fromPage: false,
      origin: ORIGIN,
    })
    expect(result).toEqual({ kind: 'files', files: [{ type: 'image/png' }] })
  })

  it('印付きはページ内でも Asset', () => {
    const result = interpretDroppedData({
      assetIdData: ASSET_ID,
      uriListData: null,
      files: [{ type: 'image/png' }],
      fromPage: true,
      origin: ORIGIN,
    })
    expect(result).toEqual({ kind: 'asset', assetId: ASSET_ID })
  })
})

describe('setAssetDragData', () => {
  it('印を付け、コピーとして運ぶ', () => {
    const data: Record<string, string> = {}
    const transfer = {
      effectAllowed: 'all' as DataTransfer['effectAllowed'],
      setData(type: string, value: string) {
        data[type] = value
      },
    }
    setAssetDragData(transfer, ASSET_ID)
    expect(data).toEqual({ [GAKEI_ASSET_ID_DATA_TYPE]: ASSET_ID })
    expect(transfer.effectAllowed).toBe('copy')
  })
})

describe('dragMayCarryImage', () => {
  it('ファイルか印を運ぶときだけ true(リンクだけでは false)', () => {
    expect(dragMayCarryImage(['Files'])).toBe(true)
    expect(dragMayCarryImage([GAKEI_ASSET_ID_DATA_TYPE])).toBe(true)
    expect(dragMayCarryImage(['text/uri-list', 'text/html'])).toBe(false)
  })
})

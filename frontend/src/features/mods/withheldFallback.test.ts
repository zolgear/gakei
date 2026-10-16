import { afterEach, describe, expect, it, vi } from 'vitest'
import { isKnownWithheld, probeWithheld, resetWithheldFallbackForTest, withheldKeyFor } from './withheldFallback'

const ID = '0f9c1d7e-1234-4abc-8def-0123456789ab'

function stubFetch(status: number) {
  const fetchMock = vi.fn(async () => new Response(status === 410 ? '{"detail":"x"}' : 'img', { status }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  resetWithheldFallbackForTest()
})

describe('withheldKeyFor', () => {
  it('本人向けと共有の配信 URL から Asset の id を取り出す', () => {
    expect(withheldKeyFor(`/api/assets/${ID}/content?variant=thumb&dv=1`)).toBe(ID)
    expect(withheldKeyFor(`/api/public/shares/tok/assets/${ID}/content?variant=preview`)).toBe(ID)
  })

  it('配信 URL でなければ URL そのもの', () => {
    expect(withheldKeyFor('blob:http://x/abc')).toBe('blob:http://x/abc')
  })
})

describe('probeWithheld', () => {
  it('410 なら覚え、ほかの版も同じ扱いにする', async () => {
    const fetchMock = stubFetch(410)
    expect(await probeWithheld(`/api/assets/${ID}/content?variant=thumb`)).toBe(true)
    expect(isKnownWithheld(`/api/assets/${ID}/content?variant=preview`)).toBe(true)
    // 覚えた後は取り直さない。
    expect(await probeWithheld(`/api/assets/${ID}/content?variant=original`)).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('410 以外は覚えない', async () => {
    stubFetch(500)
    expect(await probeWithheld(`/api/assets/${ID}/content?variant=thumb`)).toBe(false)
    expect(isKnownWithheld(`/api/assets/${ID}/content?variant=thumb`)).toBe(false)
  })

  it('通信の失敗は false', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new Error('offline'))))
    expect(await probeWithheld(`/api/assets/${ID}/content?variant=thumb`)).toBe(false)
  })

  it('同じ Asset の確認は1回にまとめる', async () => {
    const fetchMock = stubFetch(410)
    const [a, b] = await Promise.all([
      probeWithheld(`/api/assets/${ID}/content?variant=thumb`),
      probeWithheld(`/api/assets/${ID}/content?variant=preview`),
    ])
    expect([a, b]).toEqual([true, true])
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

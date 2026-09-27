import { describe, expect, it } from 'vitest'
import { shouldUsePngFavicon } from './browserSupport'

const CHROME_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
const EDGE_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0'
const FIREFOX_WINDOWS = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0'
const CHROME_ANDROID =
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36'
const CHROME_IOS =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0.6367.72 Mobile/15E148 Safari/604.1'
const SAFARI_MAC =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15'
const SAFARI_IOS =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'
const SAFARI_IPAD =
  'Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1'

describe('shouldUsePngFavicon', () => {
  it('Chrome(Windows)は false', () => {
    expect(shouldUsePngFavicon(CHROME_WINDOWS)).toBe(false)
  })

  it('Edge は false', () => {
    expect(shouldUsePngFavicon(EDGE_WINDOWS)).toBe(false)
  })

  it('Firefox は false', () => {
    expect(shouldUsePngFavicon(FIREFOX_WINDOWS)).toBe(false)
  })

  it('Android(Chrome)は false', () => {
    expect(shouldUsePngFavicon(CHROME_ANDROID)).toBe(false)
  })

  it('iOS の Chrome(CriOS)は Safari の UA を含むが false', () => {
    expect(shouldUsePngFavicon(CHROME_IOS)).toBe(false)
  })

  it('Mac の Safari は true', () => {
    expect(shouldUsePngFavicon(SAFARI_MAC)).toBe(true)
  })

  it('iOS の Safari(iPhone/iPad)は true', () => {
    expect(shouldUsePngFavicon(SAFARI_IOS)).toBe(true)
    expect(shouldUsePngFavicon(SAFARI_IPAD)).toBe(true)
  })
})

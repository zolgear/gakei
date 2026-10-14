import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ContentRatingLevel } from '../../api/client'
import { msg } from '../../i18n'
import { USER_PREFERENCES_QUERY_KEY } from '../settings/userPreferences'
import { AssetThumb } from './AssetThumb'
import { isActivationKey, remaskFromEvent } from './remask'
import { isRevealed, resetRevealed, revealAsset } from './revealStore'
import { assetMaskState } from './useAssetMasked'

// 画面のテストの仕組み(jsdom)は入れていないので、描いた HTML と、操作の処理(`remask.ts`)を
// 別々に確かめる(`ConfirmDialog.test.ts` と同じ考え方)。

afterEach(() => resetRevealed())

function render(rating: ContentRatingLevel | null | undefined, extra: Record<string, unknown> = {}) {
  const client = new QueryClient()
  // 既定の設定(R 以上をマスクし、未判定はマスクしない)。
  client.setQueryData(USER_PREFERENCES_QUERY_KEY, {})
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(AssetThumb, { assetId: 'a1', rating, alt: '', ...extra }),
    ),
  )
}

describe('assetMaskState', () => {
  it('マスクする段階で外していなければマスク、外していれば「戻す」を出す', () => {
    expect(assetMaskState(true, true, false)).toEqual({ masked: true, revealed: false })
    expect(assetMaskState(true, true, true)).toEqual({ masked: false, revealed: true })
  })

  it('マスクしない段階・id の無い画像では、どちらも出さない', () => {
    expect(assetMaskState(true, false, true)).toEqual({ masked: false, revealed: false })
    expect(assetMaskState(false, true, true)).toEqual({ masked: false, revealed: false })
  })
})

describe('AssetThumb のマスクに戻すボタン', () => {
  const label = msg().contentRating.remask

  it('マスクしているあいだは出さない', () => {
    const html = render('x')
    expect(html).toContain('data-rating-masked="true"')
    expect(html).not.toContain(`aria-label="${label}"`)
  })

  it('外した画像(マスクする段階)には、隅に目を閉じたアイコンと段階を出す', () => {
    revealAsset('a1')
    const html = render('x', { badgePosition: 'top-right' })
    expect(html).not.toContain('data-rating-masked')
    expect(html).toContain(`aria-label="${label}"`)
    expect(html).toContain('role="button"')
    expect(html).toContain('data-position="top-right"')
    expect(html).toContain('<svg')
    expect(html).toContain('>X</span>')
  })

  it('badge を付けないサムネイルにも出す', () => {
    revealAsset('a1')
    expect(render('xxx', { badge: false })).toContain(`aria-label="${label}"`)
  })

  it('隅のバッジと置き換える(二重に出さない)', () => {
    revealAsset('a1')
    const html = render('r', { badge: true })
    expect(html.match(/data-size="corner"/g)?.length).toBe(1)
  })

  it('マスクしない段階は、外していても出さない', () => {
    revealAsset('a1')
    const html = render('g', { badge: true })
    expect(html).not.toContain(`aria-label="${label}"`)
    // 普段の隅のバッジは出す。
    expect(html).toContain('data-level="g"')
  })

  it('押すとマスクに戻し、下のカードへ伝えない', () => {
    revealAsset('a1')
    const e = { preventDefault: vi.fn(), stopPropagation: vi.fn() }
    remaskFromEvent(e, 'a1')
    expect(e.preventDefault).toHaveBeenCalledTimes(1)
    expect(e.stopPropagation).toHaveBeenCalledTimes(1)
    expect(isRevealed('a1')).toBe(false)
    expect(render('x')).toContain('data-rating-masked="true"')
  })

  it('Enter と Space で押せる', () => {
    expect(isActivationKey('Enter')).toBe(true)
    expect(isActivationKey(' ')).toBe(true)
    expect(isActivationKey('Tab')).toBe(false)
  })
})

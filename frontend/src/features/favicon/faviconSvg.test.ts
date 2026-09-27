import { describe, expect, it } from 'vitest'
import { renderFaviconSvg } from './faviconSvg'
// Vite の `?raw` インポート(vite/client の型で宣言済み)でファイル内容を文字列として読む。
// Node の `fs` を使わないので、`tsconfig.app.json` に Node の型を足す必要がない。
import faviconSvgFile from '../../../public/favicon.svg?raw'

/** `frontend/public/favicon.svg` から装飾用の a11y 属性(role/aria-label/title)を取り除く。 */
function stripA11y(svg: string): string {
  return svg.replace(/ role="img" aria-label="[^"]*"/, '').replace(/<title>[^<]*<\/title>/, '')
}

describe('renderFaviconSvg', () => {
  it('idle は frontend/public/favicon.svg と同じ図形(順序・属性値とも一致)', () => {
    const favicon = stripA11y(faviconSvgFile.trim())
    expect(renderFaviconSvg({ kind: 'idle' })).toBe(favicon)
  })

  it('theme=dark/light は <style> を使わず、枠線色を直接埋め込む', () => {
    const dark = renderFaviconSvg({ kind: 'idle' }, 'dark')
    const light = renderFaviconSvg({ kind: 'idle' }, 'light')
    expect(dark).not.toContain('<style>')
    expect(dark).toContain('fill="#E8EAED"')
    expect(light).toContain('fill="#2A2D33"')
  })

  it('done(notify=true) はマスクで通知ドットの周囲を抜く', () => {
    const svg = renderFaviconSvg({ kind: 'done', notify: true })
    expect(svg).toContain('<mask id="m">')
    expect(svg).toContain('mask="url(#m)"')
    expect(svg).toContain('<circle cx="54" cy="10" r="8" fill="#FF6FA8"/>')
  })

  it('queued/fill/spin は蓋の path を含まない(蓋を外した状態)', () => {
    expect(renderFaviconSvg({ kind: 'queued' })).not.toContain('M4 4H60V9H4Z')
    expect(renderFaviconSvg({ kind: 'fill', tiles: 1 })).not.toContain('M4 4H60V9H4Z')
    expect(renderFaviconSvg({ kind: 'spin', frame: 0 })).not.toContain('M4 4H60V9H4Z')
  })

  it('error は右下だけ赤いタイル', () => {
    const svg = renderFaviconSvg({ kind: 'error' })
    expect(svg).toContain('fill="#F0525A"')
    expect(svg).toContain('M4 4H60V9H4Z') // 蓋は閉じたまま
  })
})

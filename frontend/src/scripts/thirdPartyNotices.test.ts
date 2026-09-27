/**
 * `frontend/scripts/thirdPartyNotices.lib.mjs`(第三者ライセンス表記の生成のうち、ファイル
 * I/O を伴わない純粋な部分)のテスト。`scripts/` は tsconfig.app.json の include(`src`)に
 * 入らないので、テストはこちら側(src)に置いて相対 import する(ADR-0021 4章)。
 */
import { describe, expect, it } from 'vitest'
import {
  formatNoticeEntry,
  isNoticeTarget,
  licenseOf,
  packageNameFromKey,
  renderFrontendNotices,
  urlOf,
  // @ts-expect-error -- 生の .mjs(ビルドスクリプト側)。型は無いが実行時の挙動だけを見る。
} from '../../scripts/thirdPartyNotices.lib.mjs'

describe('isNoticeTarget', () => {
  it('ルート(キーが空文字)は対象外', () => {
    expect(isNoticeTarget('', { dev: false })).toBe(false)
  })

  it('dev: true は対象外', () => {
    expect(isNoticeTarget('node_modules/vitest', { dev: true })).toBe(false)
  })

  it('optional / peer は対象に含める', () => {
    expect(isNoticeTarget('node_modules/foo', { optional: true })).toBe(true)
    expect(isNoticeTarget('node_modules/foo', { peer: true })).toBe(true)
  })

  it('通常の依存は対象', () => {
    expect(isNoticeTarget('node_modules/react', {})).toBe(true)
  })
})

describe('packageNameFromKey', () => {
  it('トップレベルのパッケージ', () => {
    expect(packageNameFromKey('node_modules/react')).toBe('react')
  })

  it('スコープ付きパッケージ', () => {
    expect(packageNameFromKey('node_modules/@fontsource/ibm-plex-sans-jp')).toBe(
      '@fontsource/ibm-plex-sans-jp',
    )
  })

  it('ネストした node_modules は最後の node_modules/ の後ろだけを取る', () => {
    expect(packageNameFromKey('node_modules/foo/node_modules/bar')).toBe('bar')
  })

  it('ネストしたスコープ付きパッケージ', () => {
    expect(packageNameFromKey('node_modules/foo/node_modules/@scope/bar')).toBe('@scope/bar')
  })
})

describe('licenseOf', () => {
  it('license が文字列ならそのまま使う', () => {
    expect(licenseOf({ license: 'MIT' })).toBe('MIT')
  })

  it('license が無ければ licenses[0].type', () => {
    expect(licenseOf({ licenses: [{ type: 'Apache-2.0' }] })).toBe('Apache-2.0')
  })

  it('どちらも無ければ UNKNOWN', () => {
    expect(licenseOf({})).toBe('UNKNOWN')
  })

  it('license が空文字なら UNKNOWN', () => {
    expect(licenseOf({ license: '  ' })).toBe('UNKNOWN')
  })
})

describe('urlOf', () => {
  it('homepage を優先する', () => {
    expect(urlOf({ homepage: 'https://example.com', repository: 'https://other.example' })).toBe(
      'https://example.com',
    )
  })

  it('homepage が無ければ repository(文字列)', () => {
    expect(urlOf({ repository: 'https://example.com/repo' })).toBe('https://example.com/repo')
  })

  it('repository がオブジェクトなら .url', () => {
    expect(urlOf({ repository: { type: 'git', url: 'https://example.com/repo.git' } })).toBe(
      'https://example.com/repo.git',
    )
  })

  it('何も無ければ null', () => {
    expect(urlOf({})).toBeNull()
  })
})

describe('formatNoticeEntry / renderFrontendNotices', () => {
  it('ライセンス本文が無ければ (no license file in the package)', () => {
    const block = formatNoticeEntry({
      name: 'foo',
      version: '1.0.0',
      license: 'MIT',
      url: 'https://example.com',
      texts: [],
    })
    expect(block).toBe('foo@1.0.0 — MIT — https://example.com\n\n(no license file in the package)')
  })

  it('url が無ければ n/a', () => {
    const block = formatNoticeEntry({
      name: 'foo',
      version: '1.0.0',
      license: 'MIT',
      url: null,
      texts: [],
    })
    expect(block).toContain('— n/a')
  })

  it('複数のライセンス本文を連結する', () => {
    const block = formatNoticeEntry({
      name: 'foo',
      version: '1.0.0',
      license: 'MIT',
      url: null,
      texts: ['本文A', '本文B'],
    })
    expect(block).toContain('本文A\n\n本文B')
  })

  it('renderFrontendNotices は見出しと ---- 区切りでまとめる', () => {
    const output = renderFrontendNotices([
      { name: 'a', version: '1.0.0', license: 'MIT', url: null, texts: [] },
      { name: 'b', version: '2.0.0', license: 'ISC', url: null, texts: [] },
    ])
    expect(output.startsWith('GAKEI frontend third-party notices\n\n')).toBe(true)
    expect(output).toContain('a@1.0.0 — MIT — n/a')
    expect(output).toContain('\n----\n')
    expect(output).toContain('b@2.0.0 — ISC — n/a')
  })
})

import { describe, expect, it } from 'vitest'
import {
  ADMIN_SETTINGS_PAGES,
  SETTINGS_PAGE_SLUGS,
  isOpenedFromToc,
  legacySettingsHashPath,
  settingsBackDestination,
  settingsPageFromSlug,
  settingsPagePath,
  settingsToc,
  visibleSettingsPages,
  type SettingsPageId,
} from './settingsPages'

const ALL = { isAdmin: true, isOidc: true, sharingEnabled: true }

describe('settingsToc / visibleSettingsPages', () => {
  it('管理者には管理者設定のページを ADR-0031 1章の順で出す', () => {
    expect(settingsToc(ALL).admin).toEqual(['openai', 'llmConnections', 'annotation', 'embeddings', 'comfyui', 'sdwebui', 'tagDictionary', 'mcp', 'shareLinks', 'authentication'])
  })

  it('非管理者には管理者設定のページを出さない(見出しごと隠せるよう空にする)', () => {
    const toc = settingsToc({ ...ALL, isAdmin: false })
    expect(toc.admin).toEqual([])
    for (const p of ADMIN_SETTINGS_PAGES) expect(visibleSettingsPages({ ...ALL, isAdmin: false })).not.toContain(p)
  })

  it('oidc モードではプロフィールを先頭、アクセストークンをユーザー設定の末尾に出す', () => {
    expect(settingsToc(ALL).user).toEqual(['profile', 'display', 'shares', 'accessTokens'])
  })

  it('個人モードではプロフィールとアクセストークンを出さない', () => {
    for (const isAdmin of [true, false]) {
      const user = settingsToc({ isAdmin, isOidc: false, sharingEnabled: true }).user
      expect(user).toEqual(['display', 'shares'])
    }
  })

  it('共有リンクが無効(または取得できていない)ときは、自分の一覧を出さない。管理者の切り替えは出す', () => {
    for (const isOidc of [true, false]) {
      const pages = visibleSettingsPages({ isAdmin: true, isOidc, sharingEnabled: false })
      expect(pages).not.toContain('shares')
      expect(pages).toContain('shareLinks')
      expect(pages).toContain('display')
    }
  })

  it('「認証」は個人モードでは常に、oidc では管理者にだけ出す(ADR-0034 5章)', () => {
    expect(visibleSettingsPages({ isAdmin: true, isOidc: false, sharingEnabled: false })).toContain('authentication')
    expect(visibleSettingsPages({ isAdmin: true, isOidc: true, sharingEnabled: false })).toContain('authentication')
    expect(visibleSettingsPages({ isAdmin: false, isOidc: true, sharingEnabled: false })).not.toContain('authentication')
  })

  it('「GAKEI について」はどの区分にも属さず、常に末尾', () => {
    for (const isAdmin of [true, false]) {
      const pages = visibleSettingsPages({ ...ALL, isAdmin })
      expect(pages[pages.length - 1]).toBe('about')
      expect(settingsToc({ ...ALL, isAdmin }).other).toEqual(['about'])
    }
  })
})

describe('settingsPagePath / settingsPageFromSlug', () => {
  it('すべてのページで行き来できる', () => {
    for (const id of Object.keys(SETTINGS_PAGE_SLUGS) as SettingsPageId[]) {
      const path = settingsPagePath(id)
      expect(path.startsWith('/settings/')).toBe(true)
      expect(settingsPageFromSlug(path.slice('/settings/'.length))).toBe(id)
    }
  })

  it('パスの綴りは ADR-0031 1章の表のとおり', () => {
    expect(settingsPagePath('accessTokens')).toBe('/settings/access-tokens')
    expect(settingsPagePath('shareLinks')).toBe('/settings/share-links')
    expect(settingsPagePath('comfyui')).toBe('/settings/comfyui')
    expect(settingsPagePath('llmConnections')).toBe('/settings/llm-connections')
    expect(settingsPagePath('authentication')).toBe('/settings/authentication')
  })

  it('知らない区切りは null', () => {
    expect(settingsPageFromSlug('unknown')).toBeNull()
    expect(settingsPageFromSlug(undefined)).toBeNull()
    expect(settingsPageFromSlug('accessTokens')).toBeNull()
  })
})

describe('legacySettingsHashPath', () => {
  it('1ページだった頃のハッシュを対応するページへ転送する', () => {
    expect(legacySettingsHashPath('#comfyui')).toBe('/settings/comfyui')
    expect(legacySettingsHashPath('#openai')).toBe('/settings/openai')
    expect(legacySettingsHashPath('#annotation')).toBe('/settings/annotation')
    expect(legacySettingsHashPath('#mcp')).toBe('/settings/mcp')
    expect(legacySettingsHashPath('#share-links')).toBe('/settings/share-links')
    expect(legacySettingsHashPath('#shares')).toBe('/settings/shares')
    expect(legacySettingsHashPath('#access-tokens')).toBe('/settings/access-tokens')
    expect(legacySettingsHashPath('mcp')).toBe('/settings/mcp')
  })

  it('知らないハッシュと空は転送しない', () => {
    expect(legacySettingsHashPath('')).toBeNull()
    expect(legacySettingsHashPath('#')).toBeNull()
    expect(legacySettingsHashPath('#nope')).toBeNull()
  })
})

describe('settingsBackDestination', () => {
  it('幅が十分なときは設定を開く前の画面に戻る(履歴が無ければホームへ)', () => {
    expect(settingsBackDestination({ isWide: true, openedFromToc: false, locationKey: 'abc' })).toEqual({ type: 'back' })
    expect(settingsBackDestination({ isWide: true, openedFromToc: true, locationKey: 'abc' })).toEqual({ type: 'back' })
    expect(settingsBackDestination({ isWide: true, openedFromToc: false, locationKey: 'default' })).toEqual({
      type: 'replace',
      path: '/',
    })
  })

  it('幅が狭いときは目次へ。目次から開いたなら1つ戻る', () => {
    expect(settingsBackDestination({ isWide: false, openedFromToc: true, locationKey: 'abc' })).toEqual({ type: 'back' })
    expect(settingsBackDestination({ isWide: false, openedFromToc: false, locationKey: 'abc' })).toEqual({
      type: 'replace',
      path: '/settings',
    })
    expect(settingsBackDestination({ isWide: false, openedFromToc: true, locationKey: 'default' })).toEqual({
      type: 'replace',
      path: '/settings',
    })
  })
})

describe('isOpenedFromToc', () => {
  it('目次の印だけを見分ける', () => {
    expect(isOpenedFromToc({ fromSettingsToc: true })).toBe(true)
    expect(isOpenedFromToc(null)).toBe(false)
    expect(isOpenedFromToc({ fromSettingsToc: 'yes' })).toBe(false)
    expect(isOpenedFromToc(undefined)).toBe(false)
  })
})

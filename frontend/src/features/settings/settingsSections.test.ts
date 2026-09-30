import { describe, expect, it } from 'vitest'
import { ADMIN_SETTINGS_SECTIONS, USER_SETTINGS_SECTIONS, visibleSections } from './settingsSections'

describe('visibleSections', () => {
  it('管理者にはユーザー設定・管理者設定・about のすべてを見せる', () => {
    const sections = visibleSections(true, false, true)
    for (const s of USER_SETTINGS_SECTIONS) expect(sections).toContain(s)
    for (const s of ADMIN_SETTINGS_SECTIONS) expect(sections).toContain(s)
    expect(sections).toContain('about')
  })

  it('非管理者には管理者設定を見せない', () => {
    const sections = visibleSections(false, false, true)
    for (const s of ADMIN_SETTINGS_SECTIONS) expect(sections).not.toContain(s)
    for (const s of USER_SETTINGS_SECTIONS) expect(sections).toContain(s)
    expect(sections).toContain('about')
  })

  it('oidc モードでは管理者・非管理者いずれもプロフィールを先頭に見せる', () => {
    expect(visibleSections(true, true, true)[0]).toBe('profile')
    expect(visibleSections(false, true, true)[0]).toBe('profile')
  })

  it('none モードではプロフィールを見せない(管理者・非管理者いずれも)', () => {
    expect(visibleSections(true, false, true)).not.toContain('profile')
    expect(visibleSections(false, false, true)).not.toContain('profile')
  })

  it('アクセストークンは oidc モードだけ、ユーザー設定の末尾(管理者設定より前)に見せる', () => {
    for (const admin of [true, false]) {
      const sections = visibleSections(admin, true, true)
      expect(sections).toContain('accessTokens')
      expect(sections.indexOf('accessTokens')).toBeGreaterThan(sections.indexOf('display'))
      expect(visibleSections(admin, false, true)).not.toContain('accessTokens')
    }
    const adminSections = visibleSections(true, true, true)
    expect(adminSections.indexOf('accessTokens')).toBeLessThan(adminSections.indexOf('apiKey'))
  })

  it('MCP は管理者設定の末尾(ComfyUI の後)で、非管理者には見せない', () => {
    const sections = visibleSections(true, false, true)
    expect(sections.indexOf('mcp')).toBe(sections.indexOf('comfyui') + 1)
    expect(visibleSections(false, true, true)).not.toContain('mcp')
  })

  it('自動タイトル・タグは管理者設定の「生成」の直後で、非管理者には見せない', () => {
    const sections = visibleSections(true, false, true)
    expect(sections.indexOf('annotation')).toBe(sections.indexOf('generation') + 1)
    expect(visibleSections(false, false, true)).not.toContain('annotation')
    expect(visibleSections(false, true, true)).not.toContain('annotation')
  })

  it('共有リンクの一覧は両方のモードのユーザー設定に、有効/無効は管理者設定の末尾に置く', () => {
    for (const oidc of [true, false]) {
      for (const admin of [true, false]) {
        const sections = visibleSections(admin, oidc, true)
        expect(sections.indexOf('shares')).toBe(sections.indexOf('display') + 1)
      }
      expect(visibleSections(false, oidc, true)).not.toContain('shareAdmin')
    }
    const sections = visibleSections(true, false, true)
    expect(sections.indexOf('shareAdmin')).toBe(sections.indexOf('mcp') + 1)
  })

  it('共有リンクが無効(または設定を取得できていない)ときは、ユーザー設定の一覧を出さない', () => {
    for (const oidc of [true, false]) {
      for (const admin of [true, false]) {
        expect(visibleSections(admin, oidc, true)).toContain('shares')
        const sections = visibleSections(admin, oidc, false)
        expect(sections).not.toContain('shares')
        // ほかのユーザー設定はそのまま
        expect(sections).toContain('language')
        expect(sections).toContain('display')
      }
    }
    // 管理者設定の有効/無効の切り替えは、無効のあいだも出す(有効に戻すため)
    expect(visibleSections(true, false, false)).toContain('shareAdmin')
    expect(visibleSections(true, true, false)).toContain('shareAdmin')
  })
})

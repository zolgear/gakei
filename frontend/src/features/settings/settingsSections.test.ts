import { describe, expect, it } from 'vitest'
import { ADMIN_SETTINGS_SECTIONS, USER_SETTINGS_SECTIONS, visibleSections } from './settingsSections'

describe('visibleSections', () => {
  it('管理者にはユーザー設定・管理者設定・about のすべてを見せる', () => {
    const sections = visibleSections(true, false)
    for (const s of USER_SETTINGS_SECTIONS) expect(sections).toContain(s)
    for (const s of ADMIN_SETTINGS_SECTIONS) expect(sections).toContain(s)
    expect(sections).toContain('about')
  })

  it('非管理者には管理者設定を見せない', () => {
    const sections = visibleSections(false, false)
    for (const s of ADMIN_SETTINGS_SECTIONS) expect(sections).not.toContain(s)
    for (const s of USER_SETTINGS_SECTIONS) expect(sections).toContain(s)
    expect(sections).toContain('about')
  })

  it('oidc モードでは管理者・非管理者いずれもプロフィールを先頭に見せる', () => {
    expect(visibleSections(true, true)[0]).toBe('profile')
    expect(visibleSections(false, true)[0]).toBe('profile')
  })

  it('none モードではプロフィールを見せない(管理者・非管理者いずれも)', () => {
    expect(visibleSections(true, false)).not.toContain('profile')
    expect(visibleSections(false, false)).not.toContain('profile')
  })
})

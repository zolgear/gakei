/**
 * 設定画面のセクション分け(ADR-0019 5章)。「ユーザー設定」(プロフィール、言語、表示)と
 * 「管理者設定」(OpenAI API キー・Base URL、生成、ComfyUI)を分け、非管理者には管理者設定の
 * セクションを出さない(代わりに `settings.adminOnly` の一文だけ出す。`SettingsPage.tsx` 側の
 * 役割)。「GAKEI について」はどちらの区分にも属さず、末尾に常に表示する。
 * 「プロフィール」(アバター、ADR-0020)は oidc モードのときだけユーザー設定の先頭に足す
 * (個人モード(`AUTH_MODE=none`)にはユーザーが無いので出さない)。
 */
export type SettingsSectionId = 'profile' | 'language' | 'display' | 'apiKey' | 'generation' | 'comfyui' | 'about'

export const USER_SETTINGS_SECTIONS: readonly SettingsSectionId[] = ['language', 'display']
export const ADMIN_SETTINGS_SECTIONS: readonly SettingsSectionId[] = ['apiKey', 'generation', 'comfyui']

/** 管理者なら全セクション、非管理者はユーザー設定と「GAKEI について」だけ。oidc モードなら先頭に「プロフィール」を足す。 */
export function visibleSections(isAdmin: boolean, isOidc: boolean): readonly SettingsSectionId[] {
  const userSections: readonly SettingsSectionId[] = isOidc
    ? ['profile', ...USER_SETTINGS_SECTIONS]
    : USER_SETTINGS_SECTIONS
  return isAdmin
    ? [...userSections, ...ADMIN_SETTINGS_SECTIONS, 'about']
    : [...userSections, 'about']
}

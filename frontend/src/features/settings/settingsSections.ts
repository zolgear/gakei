/**
 * 設定画面のセクション分け(ADR-0019 5章)。「ユーザー設定」(プロフィール、言語、表示)と
 * 「管理者設定」(OpenAI API キー・Base URL、生成、ComfyUI)を分け、非管理者には管理者設定の
 * セクションを出さない(代わりに `settings.adminOnly` の一文だけ出す。`SettingsPage.tsx` 側の
 * 役割)。「GAKEI について」はどちらの区分にも属さず、末尾に常に表示する。
 * 「プロフィール」(アバター、ADR-0020)は oidc モードのときだけユーザー設定の先頭に足す
 * (個人モード(`AUTH_MODE=none`)にはユーザーが無いので出さない)。
 * 「アクセストークン」(MCP の接続に使う、ADR-0023 6章)も oidc モードだけで、ユーザー設定の末尾に足す。
 * 「MCP」(有効/無効、上限、接続先。ADR-0023 6章)は管理者設定の末尾(ComfyUI の後)に置く。
 * 「自動タイトル・タグ」(ADR-0024 5章)は OpenAI の設定を流用するので「生成」の直後に置く。
 * 「共有リンク」(ADR-0029)は2つに分かれる。自分の共有の一覧(`shares`)は、両方のモードで
 * ユーザー設定の「表示」の後に置く(個人モードでも共有は作れる)。ただし管理者設定で無効のあいだは
 * 機能の存在ごと隠すので出さない(設定を取得できるまでと、取得に失敗したときも出さない)。
 * 有効/無効(`shareAdmin`)は外に出す機能の設定なので、管理者設定の末尾(MCP の後)に置く。
 */
export type SettingsSectionId =
  | 'profile'
  | 'language'
  | 'display'
  | 'accessTokens'
  | 'apiKey'
  | 'generation'
  | 'annotation'
  | 'comfyui'
  | 'mcp'
  | 'shares'
  | 'shareAdmin'
  | 'about'

export const USER_SETTINGS_SECTIONS: readonly SettingsSectionId[] = ['language', 'display', 'shares']
export const ADMIN_SETTINGS_SECTIONS: readonly SettingsSectionId[] = [
  'apiKey',
  'generation',
  'annotation',
  'comfyui',
  'mcp',
  'shareAdmin',
]

/**
 * 管理者なら全セクション、非管理者はユーザー設定と「GAKEI について」だけ。oidc モードなら
 * 先頭に「プロフィール」、ユーザー設定の末尾に「アクセストークン」を足す。
 * `sharingEnabled` が true(共有リンクの設定を取得でき、有効)のときだけ、ユーザー設定に
 * 自分の共有の一覧(`shares`)を出す。管理者設定の `shareAdmin` は有効/無効に関わらず出す。
 */
export function visibleSections(
  isAdmin: boolean,
  isOidc: boolean,
  sharingEnabled: boolean,
): readonly SettingsSectionId[] {
  const baseUserSections = sharingEnabled
    ? USER_SETTINGS_SECTIONS
    : USER_SETTINGS_SECTIONS.filter((s) => s !== 'shares')
  const userSections: readonly SettingsSectionId[] = isOidc
    ? ['profile', ...baseUserSections, 'accessTokens']
    : baseUserSections
  return isAdmin
    ? [...userSections, ...ADMIN_SETTINGS_SECTIONS, 'about']
    : [...userSections, 'about']
}

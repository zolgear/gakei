/**
 * 設定画面のページ分け(ADR-0031 1章。ADR-0019 5章の「ユーザー設定」「管理者設定」の括りを
 * 目次の見出しとして引き継ぐ)。どのページを出すか、ページのパス、1ページだった頃のハッシュ付きの
 * リンク(`/settings#comfyui` など)の転送先、「戻る」の行き先を、純粋関数として持つ。
 *
 * - 「プロフィール」(ADR-0020)と「アクセストークン」(ADR-0023 6章)は oidc モードのときだけ。
 * - 自分の「共有リンク」の一覧(ADR-0029)は、管理者設定で有効のときだけ(取得できるまでと、
 *   取得に失敗したときも出さない)。有効/無効の切り替え(`shareLinks`)は管理者設定で常に出す。
 * - 非管理者には管理者設定のページを出さず、見出しも出さない(無効な機能は隠す)。
 * - 「GAKEI について」はどちらの区分にも属さず、目次の末尾に置く。
 */
export type SettingsPageId =
  | 'profile'
  | 'display'
  | 'shares'
  | 'accessTokens'
  | 'openai'
  | 'llmConnections'
  | 'annotation'
  | 'embeddings'
  | 'comfyui'
  | 'mcp'
  | 'shareLinks'
  | 'about'

/** ページ → パスの最後の区切り(`/settings/{slug}`)。旧いハッシュ(`#slug`)も同じ綴り。 */
export const SETTINGS_PAGE_SLUGS: Record<SettingsPageId, string> = {
  profile: 'profile',
  display: 'display',
  shares: 'shares',
  accessTokens: 'access-tokens',
  openai: 'openai',
  llmConnections: 'llm-connections',
  annotation: 'annotation',
  embeddings: 'embeddings',
  comfyui: 'comfyui',
  mcp: 'mcp',
  shareLinks: 'share-links',
  about: 'about',
}

export const USER_SETTINGS_PAGES: readonly SettingsPageId[] = ['profile', 'display', 'shares', 'accessTokens']
export const ADMIN_SETTINGS_PAGES: readonly SettingsPageId[] = [
  'openai',
  'llmConnections',
  'annotation',
  'embeddings',
  'comfyui',
  'mcp',
  'shareLinks',
]

/** 幅が十分なとき、`/settings` で本文に出すページ。 */
export const DEFAULT_SETTINGS_PAGE: SettingsPageId = 'display'

/** 目次と本文を横に並べる、設定ページの幅の下限(px)。CSS のコンテナクエリと同じ値。 */
export const SETTINGS_WIDE_MIN_WIDTH = 720

export interface SettingsToc {
  user: readonly SettingsPageId[]
  /** 非管理者では空(見出しごと出さない)。 */
  admin: readonly SettingsPageId[]
  other: readonly SettingsPageId[]
}

export interface SettingsVisibility {
  isAdmin: boolean
  isOidc: boolean
  /** 共有リンクの設定を取得でき、有効になっているか。 */
  sharingEnabled: boolean
}

/** 目次に出すページを区分ごとに返す。 */
export function settingsToc({ isAdmin, isOidc, sharingEnabled }: SettingsVisibility): SettingsToc {
  const user = USER_SETTINGS_PAGES.filter((p) => {
    if (p === 'profile' || p === 'accessTokens') return isOidc
    if (p === 'shares') return sharingEnabled
    return true
  })
  return { user, admin: isAdmin ? ADMIN_SETTINGS_PAGES : [], other: ['about'] }
}

/** 見せてよいページを目次の順に並べたもの。 */
export function visibleSettingsPages(visibility: SettingsVisibility): readonly SettingsPageId[] {
  const toc = settingsToc(visibility)
  return [...toc.user, ...toc.admin, ...toc.other]
}

export function settingsPagePath(page: SettingsPageId): string {
  return `/settings/${SETTINGS_PAGE_SLUGS[page]}`
}

/** パスの区切り(`:page`)からページを引く。知らないものは null。 */
export function settingsPageFromSlug(slug: string | undefined): SettingsPageId | null {
  if (!slug) return null
  const entry = Object.entries(SETTINGS_PAGE_SLUGS).find(([, s]) => s === slug)
  return entry ? (entry[0] as SettingsPageId) : null
}

/**
 * 1ページだった頃のハッシュ付きのリンク(`/settings#comfyui` など)の転送先。知らないハッシュは null
 * (転送せず、そのまま `/settings` を出す)。`hash` は先頭の `#` があってもなくてもよい。
 */
export function legacySettingsHashPath(hash: string): string | null {
  const page = settingsPageFromSlug(hash.replace(/^#/, ''))
  return page ? settingsPagePath(page) : null
}

/**
 * ページの「戻る」の行き先(ADR-0031 3章)。
 * - 幅が十分なとき: 設定を開く前の画面に戻る(目次での移動は履歴を置き換えるので、1つ戻れば足りる)。
 *   履歴が無ければ(直接開いた・再読み込み直後)ホームへ置き換える(`useBackNavigate` と同じ)。
 * - 幅が狭いとき: 目次へ。目次から開いたなら1つ戻り、そうでなければ目次へ置き換える。
 */
export type SettingsBackDestination = { type: 'back' } | { type: 'replace'; path: string }

export function settingsBackDestination(params: {
  isWide: boolean
  /** 狭い幅の目次から開いたか(目次のリンクが履歴の state に印を付ける)。 */
  openedFromToc: boolean
  /** react-router の `location.key`。直接開いたときは `'default'`。 */
  locationKey: string
}): SettingsBackDestination {
  const { isWide, openedFromToc, locationKey } = params
  if (isWide) {
    return locationKey === 'default' ? { type: 'replace', path: '/' } : { type: 'back' }
  }
  return openedFromToc && locationKey !== 'default' ? { type: 'back' } : { type: 'replace', path: '/settings' }
}

/** 目次のリンクが履歴の state に付ける印。 */
export const SETTINGS_TOC_STATE = { fromSettingsToc: true } as const

export function isOpenedFromToc(state: unknown): boolean {
  return typeof state === 'object' && state !== null && (state as Record<string, unknown>).fromSettingsToc === true
}

/** 設定画面と、キー未設定バナー(AppShell)で共有する React Query のキー。 */
export const OPENAI_KEY_STATUS_QUERY_KEY = ['openai-key-status'] as const

/** OpenAI の接続先(Base URL)の状態(ADR-0017)。設定画面だけが読む。 */
export const OPENAI_BASE_URL_STATUS_QUERY_KEY = ['openai-base-url-status'] as const

/**
 * `GET /api/settings/general` のキー。OpenAI のページ(moderation)と ComfyUI のページ(タイムアウト)が
 * それぞれ読む。どちらも保存すると、応答でこのキャッシュを置き換える。
 */
export const GENERAL_SETTINGS_QUERY_KEY = ['general-settings'] as const

/** `GET /api/about`(ADR-0021 3章)。「GAKEI について」だけが読む。 */
export const ABOUT_QUERY_KEY = ['about'] as const

/** `GET /api/settings/mcp`(ADR-0023)。管理者設定の「MCP」のページだけが読む。 */
export const MCP_SETTINGS_QUERY_KEY = ['mcp-settings'] as const

/** `GET /api/users/me/api-tokens`(ADR-0023)。ユーザー設定の「アクセストークン」だけが読む。 */
export const API_TOKENS_QUERY_KEY = ['api-tokens'] as const

/**
 * `GET /api/settings/annotation`(ADR-0024)。管理者設定の「自動タイトル・タグ」と、ビューアの
 * 「再推定」(使えるエンジンがあるか)が読む。
 */
export const ANNOTATION_SETTINGS_QUERY_KEY = ['annotation-settings'] as const

/**
 * `GET /api/settings/llm-connections`(ADR-0032)。管理者設定の「LLM の接続先」と、
 * 「自動タイトル・タグ」の使い方の表(接続先の選択肢)が読む。
 */
export const LLM_CONNECTIONS_QUERY_KEY = ['llm-connections'] as const

/**
 * `GET /api/settings/share`(ADR-0029)。管理者設定の「共有リンク」と、ビューアの「共有」
 * ボタン(有効なときだけ出す)、ユーザー設定の「共有リンク」(無効の注記)が読む。
 */
export const SHARE_SETTINGS_QUERY_KEY = ['share-settings'] as const

/** `GET /api/shares`(ADR-0029)。ユーザー設定の「共有リンク」の一覧。 */
export const SHARES_QUERY_KEY = ['shares'] as const

/**
 * `GET /api/settings/embeddings`(ADR-0033)。管理者設定の「埋め込み」と、重複の候補のページ
 * (しきい値の初期値)が読む。
 */
export const EMBEDDING_SETTINGS_QUERY_KEY = ['embedding-settings'] as const

/** `GET /api/auth/me`(ADR-0019)。`AuthGate` が読む。認証の設定を保存したら取り直す。 */
export const AUTH_ME_QUERY_KEY = ['auth-me'] as const

/** `GET /api/settings/auth`(ADR-0034)。管理者設定の「認証」だけが読む。 */
export const AUTH_SETTINGS_QUERY_KEY = ['auth-settings'] as const

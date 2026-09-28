/** 設定画面と、キー未設定バナー(AppShell)で共有する React Query のキー。 */
export const OPENAI_KEY_STATUS_QUERY_KEY = ['openai-key-status'] as const

/** OpenAI の接続先(Base URL)の状態(ADR-0017)。設定画面だけが読む。 */
export const OPENAI_BASE_URL_STATUS_QUERY_KEY = ['openai-base-url-status'] as const

/**
 * `GET /api/settings/general` のキー。「生成」セクション(moderation)と ComfyUI セクションの
 * タイムアウト欄がそれぞれ独立に読む(`ComfyUIStatusPanel` と `comfyui-status` の関係と同じで、
 * 同じキーで問い合わせが1回にまとまる)。
 */
export const GENERAL_SETTINGS_QUERY_KEY = ['general-settings'] as const

/** `GET /api/about`(ADR-0021 3章)。「GAKEI について」だけが読む。 */
export const ABOUT_QUERY_KEY = ['about'] as const

/** `GET /api/settings/mcp`(ADR-0023)。管理者設定の「MCP」セクションだけが読む。 */
export const MCP_SETTINGS_QUERY_KEY = ['mcp-settings'] as const

/** `GET /api/users/me/api-tokens`(ADR-0023)。ユーザー設定の「アクセストークン」だけが読む。 */
export const API_TOKENS_QUERY_KEY = ['api-tokens'] as const

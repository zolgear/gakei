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

/**
 * `GET /api/sdwebui/status`(ADR-0038)。管理者設定の「SD WebUI」が読む。接続・資格情報・一覧の
 * 読み直しのあとは応答でこのキャッシュを置き換え、capabilities も取り直す。
 */
export const SDWEBUI_STATUS_QUERY_KEY = ['sdwebui-status'] as const

/**
 * `GET /api/settings/assistant`(ADR-0048)。管理者設定の「アシスタント」と、アイコンレール
 * (使えるときだけアシスタントのパネルを出す)が読む。
 */
export const ASSISTANT_SETTINGS_QUERY_KEY = ['assistant-settings'] as const

/**
 * `GET /api/settings/provider-localities`(ADR-0051 3章)。OpenAI・ComfyUI・SD WebUI のページの
 * 「接続先」のカードが読む。
 */
export const PROVIDER_LOCALITIES_QUERY_KEY = ['provider-localities'] as const

/** `GET /api/settings/mods`(ADR-0051 7章)。管理者設定の「Mod」だけが読む。 */
export const MODS_QUERY_KEY = ['mods'] as const

/** `GET /api/settings/mods/invocations`。直近の呼び出し(失敗だけに絞るかを後ろに足す)。 */
export const MOD_INVOCATIONS_QUERY_KEY = ['mod-invocations'] as const

/** `GET /api/settings/mods/deliveries?status=failed`。配達の失敗。 */
export const MOD_DELIVERY_FAILURES_QUERY_KEY = ['mod-delivery-failures'] as const

/**
 * `GET /api/settings/region-detection`(ADR-0053 7章)。管理者設定の「領域の検出」と、「領域」の
 * 画面の検出のボタンの出し分けが同じキーを読む。
 */
export const REGION_DETECTION_SETTINGS_QUERY_KEY = ['region-detection-settings'] as const

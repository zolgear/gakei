/**
 * 管理者設定の「MCP」とユーザー設定の「アクセストークン」(ADR-0023 6章)が使う、表示・検証の
 * 純粋関数。API 呼び出しや状態管理は `pages/McpSettingsPage.tsx` / `ApiTokensSection.tsx` が行う。
 */
import { formatDateTime } from '../../lib/format'

/** MCP の登録名。`claude mcp add` の第1引数で、エージェント側のツール名の接頭辞になる。 */
export const MCP_SERVER_NAME = 'gakei'

/** 1 時間あたりの上限の下限。0 は「MCP 経由の生成を止める」意味(読み取り系のツールは使える)。 */
export const HOURLY_RUN_LIMIT_MIN = 0

/** トークン名の長さの上限(`backend/app/domain/api_tokens.py` と同じ)。 */
export const API_TOKEN_NAME_MAX_LENGTH = 100

/**
 * Claude Code に登録するコマンド例。認証モード(oidc)ではアクセストークンを
 * `Authorization: Bearer` ヘッダーで渡す必要があるので、ヘッダー付きの形にする。
 * `tokenPlaceholder` は「<トークン>」のような、利用者が置き換える部分の表記。
 */
export function buildClaudeMcpAddCommand(endpointUrl: string, requiresToken: boolean, tokenPlaceholder: string): string {
  const base = `claude mcp add --transport http ${MCP_SERVER_NAME} ${endpointUrl}`
  if (!requiresToken) return base
  return `${base} --header "Authorization: Bearer ${tokenPlaceholder}"`
}

/** 上限の入力欄の値が保存できるか(0〜max の整数)。 */
export function isValidHourlyLimitInput(input: string, max: number): boolean {
  const trimmed = input.trim()
  if (!/^\d+$/.test(trimmed)) return false
  const value = Number(trimmed)
  return Number.isSafeInteger(value) && value >= HOURLY_RUN_LIMIT_MIN && value <= max
}

/** トークン名が発行できるか(前後の空白を除いて 1〜100 文字)。 */
export function isValidApiTokenName(name: string): boolean {
  const trimmed = name.trim()
  return trimmed.length > 0 && trimmed.length <= API_TOKEN_NAME_MAX_LENGTH
}

/** 最終使用日時の表示。一度も使われていなければ `neverLabel`(「未使用」)。 */
export function formatLastUsed(lastUsedAt: string | null | undefined, neverLabel: string): string {
  return lastUsedAt ? formatDateTime(lastUsedAt) : neverLabel
}

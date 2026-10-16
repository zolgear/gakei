/** error_code を人が読める文言に変換する。Run詳細と状態パネルで共有する。 */
import { msg } from '../../i18n'

/**
 * 既知の error_code に対する見出し。ここに無いコードは runStatus.errorFallback を使う。
 * ADR-0015: サーバーが書く `message` は日本語のことがある(runner はリクエストの外で動く)ので、
 * headline は必ず現在の言語で表示し、message はその詳細として添える。
 */
const KNOWN_CODES = [
  'contentFilter',
  'rateLimited',
  'interrupted',
  'missingApiKey',
  'authError',
  'connectionError',
  'providerError',
  'providerUnavailable',
  'comfyuiUnavailable',
  'comfyuiValidation',
  // SD WebUI(ADR-0038 5章)
  'sdwebuiUnavailable',
  'sdwebuiValidation',
  'sdwebuiNoOutput',
  'sdwebuiModelMismatch',
  // Mod の送る前のチェック(ADR-0051 2章)。サーバーのコードは `hook_denied`(`CODE_ALIASES`)。
  'hookDenied',
  'internalError',
] as const

type KnownErrorCode = (typeof KNOWN_CODES)[number]

/** 辞書のキーと綴りの違うサーバーのコード。 */
const CODE_ALIASES: Record<string, KnownErrorCode> = { hook_denied: 'hookDenied' }

function knownErrorCode(code: string | null | undefined): KnownErrorCode | null {
  if (!code) return null
  const key = CODE_ALIASES[code] ?? code
  return (KNOWN_CODES as readonly string[]).includes(key) ? (key as KnownErrorCode) : null
}

const NO_DETAIL_CODES = new Set<KnownErrorCode>(['contentFilter', 'rateLimited', 'interrupted'])

export function describeError(code: string | null | undefined, message: string | null | undefined): string {
  const t = msg().runStatus.errorCodes
  const known = knownErrorCode(code)
  if (known) {
    const headline = t[known]
    // `hook_denied` の message は「{Mod} が送信を止めました: {理由}」(サーバーの言語)。理由を読めるように添える。
    if (NO_DETAIL_CODES.has(known) || !message) return headline
    return `${headline}: ${message}`
  }
  return message ?? msg().runStatus.errorFallback
}

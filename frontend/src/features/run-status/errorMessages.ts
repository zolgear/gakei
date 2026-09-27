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
  'internalError',
] as const

type KnownErrorCode = (typeof KNOWN_CODES)[number]

function isKnownErrorCode(code: string | null | undefined): code is KnownErrorCode {
  return (KNOWN_CODES as readonly string[]).includes(code ?? '')
}

const NO_DETAIL_CODES = new Set<KnownErrorCode>(['contentFilter', 'rateLimited', 'interrupted'])

export function describeError(code: string | null | undefined, message: string | null | undefined): string {
  const t = msg().runStatus.errorCodes
  if (isKnownErrorCode(code)) {
    const headline = t[code]
    if (NO_DETAIL_CODES.has(code) || !message) return headline
    return `${headline}: ${message}`
  }
  return message ?? msg().runStatus.errorFallback
}

/**
 * タグの日本語訳(ADR-0041 4章)をまとめて引くフック。表示だけの補助で、タグそのものは変えない。
 *
 * - 画面の言語が日本語で、ユーザー設定「タグの日本語訳を表示」がオンのときだけ引く。
 * - 引いた結果(訳が無かったことも含む)は、名前ごとにモジュールのキャッシュに持ち、まだ引いていない
 *   名前だけを `POST /api/tags/translations` で送る(200 件ずつ)。辞書を登録・切り替え・削除したら
 *   `clearTagTranslationCache()` で捨てる。
 * - 辞書が無ければ空の結果が返るだけ(何も出さない)。
 */
import { useQuery } from '@tanstack/react-query'
import { getTagTranslations } from '../../api/client'
import { useI18n } from '../../i18n'
import { shouldShowTagTranslations, useShowTagTranslationsPref } from './tagCompletionPrefs'

const BATCH_SIZE = 200

/** 名前 → 訳(訳が無ければ null)。 */
const cache = new Map<string, string | null>()
/** キャッシュを捨てるたびに進め、問い合わせのキーに含める(古い結果を使わないため)。 */
let generation = 0

export const TAG_TRANSLATIONS_QUERY_KEY = ['tags', 'translations'] as const

export function clearTagTranslationCache(): void {
  cache.clear()
  generation++
}

async function fetchTranslations(names: readonly string[]): Promise<Record<string, string>> {
  const missing = names.filter((name) => !cache.has(name))
  for (let i = 0; i < missing.length; i += BATCH_SIZE) {
    const batch = missing.slice(i, i + BATCH_SIZE)
    const response = await getTagTranslations(batch)
    const found = response.translations ?? {}
    for (const name of batch) cache.set(name, found[name] ?? null)
  }
  const result: Record<string, string> = {}
  for (const name of names) {
    const value = cache.get(name)
    if (value) result[name] = value
  }
  return result
}

/** 訳を添えるか(言語と設定)。 */
export function useTagTranslationsEnabled(): boolean {
  const { locale } = useI18n()
  return shouldShowTagTranslations(locale, useShowTagTranslationsPref())
}

/**
 * `names` の訳。訳を出さない設定・言語のときと、まだ引けていないときは空。
 * 名前は渡したまま(空白でも `_` でもよい)をキーにする。
 */
export function useTagTranslations(names: readonly string[]): Record<string, string> {
  const enabled = useTagTranslationsEnabled()
  const unique = Array.from(new Set(names.filter((name) => name.trim().length > 0))).sort()
  const query = useQuery({
    queryKey: [...TAG_TRANSLATIONS_QUERY_KEY, generation, unique.join('\n')],
    queryFn: () => fetchTranslations(unique),
    enabled: enabled && unique.length > 0,
    staleTime: Infinity,
    // 表示の補助なので、失敗しても何も出さないだけにする。
    retry: false,
    placeholderData: (previous) => previous,
  })
  if (!enabled) return EMPTY
  return query.data ?? EMPTY
}

const EMPTY: Record<string, string> = {}

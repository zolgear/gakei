/**
 * 言語を切り替えたら、配下を描き直し、サーバーから取得したデータ(capabilities の見出しや
 * エラー文言は `Accept-Language` で変わる)を取り直す。`msg()` を使う関数やメモ化した値が
 * 古い言語のまま残らないよう、`key` で配下を作り直す(フォームの内容は localStorage に
 * 残るので失われない)。
 */
import { Fragment, useEffect, useRef, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocale } from '.'

export function LocaleRoot({ children }: { children: ReactNode }) {
  const locale = useLocale()
  const queryClient = useQueryClient()
  const previous = useRef(locale)

  useEffect(() => {
    if (previous.current === locale) return
    previous.current = locale
    void queryClient.invalidateQueries()
  }, [locale, queryClient])

  return <Fragment key={locale}>{children}</Fragment>
}

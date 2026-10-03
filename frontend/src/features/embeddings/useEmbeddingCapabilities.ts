/**
 * 埋め込みが使えるか(ADR-0033 7章の capabilities)。画面の入口(検索の「意味」、ビューアの
 * 「似た画像」、ストックの「重複の候補」)の出し分けに使う。取得できるまでと失敗したときは
 * 使えない扱いにする(無効な機能は隠す側に倒す)。
 */
import { useQuery } from '@tanstack/react-query'
import { getCapabilities, type EmbeddingCapabilities } from '../../api/client'

export interface EmbeddingCapabilitiesState {
  /** 使えるときだけ値が入る。 */
  embeddings: EmbeddingCapabilities | null
  /** capabilities をまだ取得していない(使えるかどうか決められない)。 */
  isLoading: boolean
}

export function useEmbeddingCapabilitiesState(): EmbeddingCapabilitiesState {
  const query = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const embeddings = query.data?.embeddings
  return { embeddings: embeddings?.available ? embeddings : null, isLoading: query.isLoading }
}

export function useEmbeddingCapabilities(): EmbeddingCapabilities | null {
  return useEmbeddingCapabilitiesState().embeddings
}

/**
 * 検索結果のプロンプトセット項目は snippet(前後を「…」で切り詰めた抜粋)しか持たないため、
 * フォームへ読み込む前に本文全体を取得する。`['prompt-sets']` は既存のサイドバーパネルと
 * 同じキャッシュを使うので、開いていれば通信は発生しない。
 */
import type { QueryClient } from '@tanstack/react-query'
import { listPromptSets, type PromptSetListResponse } from '../../api/client'

export async function resolvePromptSetItemText(
  queryClient: QueryClient,
  promptSetId: string,
  itemId: string,
): Promise<string | null> {
  const data = await queryClient.ensureQueryData<PromptSetListResponse>({
    queryKey: ['prompt-sets'],
    queryFn: listPromptSets,
  })
  const set = data.items?.find((s) => s.id === promptSetId)
  const item = set?.items?.find((i) => i.id === itemId)
  return item?.text ?? null
}

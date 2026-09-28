/**
 * 「+」(新しいグループ)と名前の入力(`NewGroupInline.tsx`)の状態と送信(ADR-0022 4章)。
 * ストックの「グループなし」の見出しと生成フォームの「グループ」で共有する。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, createAssetGroup, type AssetGroupRow } from '../../../api/client'
import { useI18n } from '../../../i18n'
import { invalidateAssetGroupQueries } from './assetGroupQueries'

export interface NewGroupInlineState {
  creating: boolean
  draft: string
  error: string | null
  isPending: boolean
  /** 「+」を押したとき。閉じていれば開き、開いていれば取り消す。 */
  toggle: () => void
  cancel: () => void
  setDraft: (value: string) => void
  submit: () => void
}

/**
 * 作成の状態と送信。作成に成功したら `onCreated`(呼び出し側の後始末。キャッシュへの差し込みや
 * 選択など)→ 入力を閉じる → グループ関連のクエリを作り直す、の順に行う。
 */
export function useNewGroupInline(onCreated?: (group: AssetGroupRow) => void): NewGroupInlineState {
  const { t } = useI18n()
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [draft, setDraftRaw] = useState('')
  const [error, setError] = useState<string | null>(null)

  function cancel() {
    setCreating(false)
    setDraftRaw('')
    setError(null)
  }

  const mutation = useMutation({
    mutationFn: (name: string) => createAssetGroup(name),
    onSuccess: (group) => {
      onCreated?.(group)
      cancel()
      invalidateAssetGroupQueries(queryClient)
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : t.stock.groups.createFailed),
  })

  return {
    creating,
    draft,
    error,
    isPending: mutation.isPending,
    toggle: () => (creating ? cancel() : setCreating(true)),
    cancel,
    setDraft: (value: string) => {
      setDraftRaw(value)
      setError(null)
    },
    submit: () => {
      const name = draft.trim()
      if (!name || mutation.isPending) return
      mutation.mutate(name)
    },
  }
}

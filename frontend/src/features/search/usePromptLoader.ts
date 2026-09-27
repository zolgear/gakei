/**
 * 検索結果のプロンプトセット項目を、フォームの prompt に読み込む処理(ポップオーバー・検索ページ
 * の両方で使う)。入力中のプロンプトがあれば、ブラウザの `confirm` は使わず、呼び出し側が
 * `ConfirmDialog` で「置き換える / 追加しない」を出せるように `confirmOpen` を返す。
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { useRunFormContext } from '../../context/useRunFormContext'
import { resolvePromptSetItemText } from './promptSetItemResolver'
import { msg } from '../../i18n'

export interface PromptLoader {
  requestLoad: (promptSetId: string, itemId: string) => Promise<void>
  confirmOpen: boolean
  confirmReplace: () => void
  cancelReplace: () => void
  error: string | null
}

export function usePromptLoader(onLoaded?: () => void): PromptLoader {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { formState, setFormState } = useRunFormContext()
  const [pendingText, setPendingText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  function apply(text: string) {
    setFormState({ ...formState, prompt: text })
    navigate('/studio')
    onLoaded?.()
  }

  async function requestLoad(promptSetId: string, itemId: string) {
    setError(null)
    const text = await resolvePromptSetItemText(queryClient, promptSetId, itemId)
    if (text === null) {
      setError(msg().search.itemNotFound)
      return
    }
    if (formState.prompt.trim().length > 0) {
      setPendingText(text)
      return
    }
    apply(text)
  }

  function confirmReplace() {
    if (pendingText === null) return
    apply(pendingText)
    setPendingText(null)
  }

  function cancelReplace() {
    setPendingText(null)
  }

  return { requestLoad, confirmOpen: pendingText !== null, confirmReplace, cancelReplace, error }
}

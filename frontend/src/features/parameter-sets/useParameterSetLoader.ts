/**
 * パラメーターセットをフォームに読み込む(ADR-0040 4章)。フォームの「設定を読み込む」とサイドバーの
 * パネルで共通。
 *
 * - プロバイダーが有効でなければ読み込まず、理由を返す。
 * - セットにプロンプトがあり、今のプロンプト(context の `formState.prompt`)が空でなく違うときは、
 *   確かめてから置き換える(`pending` に積み、呼び出し側が確認を出す)。
 * - 反映は画像からの読み込み(ADR-0038 9章)と同じく `requestFormLoad` に積み、スタジオ
 *   (`useRunFormLogic`)が消費する。スタジオの外からなら /studio へ移る。
 */
import { useCallback, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { getCapabilities, type ParameterSetResponse } from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { shouldConfirmReplace } from '../run-form/promptInsertion'
import { parameterSetUnavailableReason } from './parameterSets'

export function needsPromptConfirm(set: Pick<ParameterSetResponse, 'prompt'>, currentPrompt: string): boolean {
  return set.prompt !== null && shouldConfirmReplace(currentPrompt) && set.prompt !== currentPrompt
}

export function useParameterSetLoader(onApplied?: () => void) {
  const { formState, requestFormLoad } = useRunFormContext()
  const capsQuery = useQuery({ queryKey: ['capabilities'], queryFn: getCapabilities })
  const caps = capsQuery.data
  const location = useLocation()
  const navigate = useNavigate()
  const [pending, setPending] = useState<ParameterSetResponse | null>(null)

  const apply = useCallback(
    (set: ParameterSetResponse) => {
      requestFormLoad({ kind: 'parameterSet', set })
      setPending(null)
      if (location.pathname !== '/studio') navigate('/studio')
      onApplied?.()
    },
    [requestFormLoad, location.pathname, navigate, onApplied],
  )

  /** 読み込む。読み込めなければ理由を返す(確認を待つときは null)。 */
  const load = useCallback(
    (set: ParameterSetResponse): string | null => {
      const reason = parameterSetUnavailableReason(set, caps)
      if (reason !== null) return reason
      if (needsPromptConfirm(set, formState.prompt)) setPending(set)
      else apply(set)
      return null
    },
    [caps, formState.prompt, apply],
  )

  return {
    caps,
    load,
    pending,
    confirm: () => {
      if (pending) apply(pending)
    },
    cancel: () => setPending(null),
  }
}

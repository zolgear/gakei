/**
 * 生成のフォームの IP の申告(ADR-0059 5章)。入力の画像が IP を引き継ぐか、選べる用途は何かを
 * サーバーに尋ね、用途と作業のメモを持つ。IP 素材の機能が使えないときは何もしない。
 *
 * 申告が要るのに用途を選んでいない、または使えない理由があるときは `blockReason` を返す(送れない)。
 * 選んだ用途が選べる用途から外れたら(入力を変えたなど)、選び直してもらう。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { checkIpUsage, type IpUsageCheckResponse } from '../../api/client'
import { useI18n } from '../../i18n'
import { IP_QUERY_KEY, useIpAvailable } from './ipQueries'

export interface IpDeclaration {
  required: boolean
  check: IpUsageCheckResponse | null
  purpose: string
  setPurpose: (purpose: string) => void
  workNote: string
  setWorkNote: (note: string) => void
  /** 送れない理由(送れるなら null)。 */
  blockReason: string | null
  /** 送る本文に足す申告(申告が要らなければ undefined)。 */
  body: { purpose: string; work_note: string | null } | undefined
}

export function useIpDeclaration(assetIds: readonly string[], provider: string, model: string): IpDeclaration {
  const { t } = useI18n()
  const available = useIpAvailable()
  const ids = [...new Set(assetIds)].sort()
  const query = useQuery({
    queryKey: [...IP_QUERY_KEY, 'usage-check', ids, provider, model],
    queryFn: () => checkIpUsage({ asset_ids: ids, provider, model }),
    enabled: available && ids.length > 0,
    staleTime: 15_000,
  })
  const [purpose, setPurpose] = useState('')
  const [workNote, setWorkNote] = useState('')
  const check = available && ids.length > 0 ? (query.data ?? null) : null
  const required = check?.required === true
  const purposes = check?.purposes ?? []
  const purposeValid = required && purposes.includes(purpose)
  const problem = (check?.ips ?? []).find((ip) => ip.problem)?.problem ?? null

  let blockReason: string | null = null
  if (required) {
    if (problem) blockReason = problem
    else if (purposes.length === 0) blockReason = t.ip.declaration.noCommonPurpose
    else if (!purposeValid) blockReason = t.ip.declaration.choosePurpose
  } else if (available && ids.length > 0 && query.isLoading) {
    blockReason = t.ip.declaration.checking
  }

  return {
    required,
    check,
    purpose: purposeValid ? purpose : '',
    setPurpose,
    workNote,
    setWorkNote,
    blockReason,
    body: required && purposeValid ? { purpose, work_note: workNote.trim() || null } : undefined,
  }
}

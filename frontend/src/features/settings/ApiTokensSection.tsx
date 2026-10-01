/**
 * 設定 →「ユーザー設定」の「アクセストークン」(ADR-0023 6章)。AI エージェントを MCP で接続する
 * ときに `Authorization: Bearer` で渡すトークンを、各自が発行・失効させる。oidc モードのときだけ
 * 描画される(設定画面の `settingsToc` が制御する。none モードは API も 404)。
 * - 発行: 名前を付けて発行し、値はその応答にだけ載るので、この部品の state にだけ置いて1回だけ
 *   見せる(コピーボタン付き)。画面を離れると消え、再表示できない。
 * - 一覧: 名前、作成日時、最終使用日時(未使用なら「未使用」)。
 * - 失効: 取り消せない操作なので、既存の削除と同じく `ConfirmDialog` で確認してから行う。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  createApiToken,
  listApiTokens,
  revokeApiToken,
  type ApiTokenCreateResponse,
  type ApiTokenRow,
} from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import type { UseToastResult } from '../../components/Toast'
import { formatDateTime } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { CopyableValue } from './CopyableValue'
import { API_TOKENS_QUERY_KEY } from './queryKeys'
import { API_TOKEN_NAME_MAX_LENGTH, formatLastUsed, isValidApiTokenName } from './mcpSettings'
import styles from './ApiTokensSection.module.css'
import common from './settings.module.css'

interface ApiTokensSectionProps {
  toast: UseToastResult
}

export function ApiTokensSection({ toast }: ApiTokensSectionProps) {
  const { t } = useI18n()
  const a = t.settings.accessTokens
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: API_TOKENS_QUERY_KEY, queryFn: listApiTokens })
  const [nameInput, setNameInput] = useState('')
  const [created, setCreated] = useState<ApiTokenCreateResponse | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<ApiTokenRow | null>(null)

  const createMutation = useMutation({
    mutationFn: createApiToken,
    onSuccess: (data) => {
      setCreated(data)
      setNameInput('')
      void queryClient.invalidateQueries({ queryKey: API_TOKENS_QUERY_KEY })
      toast.show({ message: a.createdToast })
    },
  })

  const revokeMutation = useMutation({
    mutationFn: revokeApiToken,
    onSuccess: (_data, tokenId) => {
      // 失効させたトークンの値がまだ表示中なら、一緒に消す。
      setCreated((prev) => (prev?.id === tokenId ? null : prev))
      void queryClient.invalidateQueries({ queryKey: API_TOKENS_QUERY_KEY })
      toast.show({ message: a.revokedToast })
    },
  })

  const activeError = createMutation.error ?? revokeMutation.error
  const errorMessage =
    activeError instanceof ApiError ? activeError.message : activeError ? a.communicationFailed : null
  const nameValid = isValidApiTokenName(nameInput)

  function handleCreate() {
    if (!nameValid) return
    createMutation.mutate(nameInput.trim())
  }

  const items = query.data?.items ?? []

  return (
    <section id="access-tokens" className={common.section}>

      <form
        className={styles.form}
        onSubmit={(e) => {
          e.preventDefault()
          handleCreate()
        }}
      >
        <label htmlFor="gakei-api-token-name" title={a.nameTooltip}>
          {a.nameLabel}
        </label>
        <div className={styles.row}>
          <input
            id="gakei-api-token-name"
            type="text"
            className={common.input}
            value={nameInput}
            maxLength={API_TOKEN_NAME_MAX_LENGTH}
            placeholder={a.namePlaceholder}
            title={a.nameTooltip}
            autoComplete="off"
            disabled={createMutation.isPending}
            onChange={(e) => setNameInput(e.target.value)}
          />
          <button type="submit" className={common.primaryButton} disabled={!nameValid || createMutation.isPending}>
            {createMutation.isPending ? a.creating : a.create}
          </button>
        </div>
      </form>

      {created && (
        <div className={styles.createdBox}>
          <p className={styles.createdHeading}>{fmt(a.createdHeading, { name: created.name })}</p>
          <CopyableValue
            value={created.token}
            copyLabel={a.copy}
            copiedMessage={a.copiedToast}
            copyFailedMessage={a.copyFailed}
            toast={toast}
          />
          <p className={common.warningText}>{a.createdWarning}</p>
          <button type="button" className={common.secondaryButton} onClick={() => setCreated(null)}>
            {a.dismiss}
          </button>
        </div>
      )}

      {errorMessage && <p className={common.errorText}>{errorMessage}</p>}

      {query.isLoading && <p className={common.placeholder}>{a.loading}</p>}

      {query.isError && (
        <div className={common.loadError}>
          <p className={common.errorText}>{a.loadFailed}</p>
          <button type="button" className={common.secondaryButton} onClick={() => void query.refetch()}>
            {a.retry}
          </button>
        </div>
      )}

      {query.data && items.length === 0 && <p className={common.placeholder}>{a.empty}</p>}

      {items.length > 0 && (
        <ul className={common.list}>
          {items.map((item) => (
            <li key={item.id} className={common.item}>
              <div className={common.itemText}>
                <span className={common.itemName}>{item.name}</span>
                <dl className={common.itemMeta}>
                  <dt>{a.createdAt}</dt>
                  <dd>{formatDateTime(item.created_at)}</dd>
                  <dt>{a.lastUsedAt}</dt>
                  <dd data-never={item.last_used_at ? undefined : 'true'}>
                    {formatLastUsed(item.last_used_at, a.neverUsed)}
                  </dd>
                </dl>
              </div>
              <button
                type="button"
                className={common.dangerButton}
                disabled={revokeMutation.isPending}
                onClick={() => setRevokeTarget(item)}
              >
                {a.revoke}
              </button>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={revokeTarget !== null}
        message={revokeTarget ? fmt(a.revokeConfirm.message, { name: revokeTarget.name }) : ''}
        confirmLabel={a.revokeConfirm.confirmLabel}
        onConfirm={() => {
          if (revokeTarget) revokeMutation.mutate(revokeTarget.id)
          setRevokeTarget(null)
        }}
        onCancel={() => setRevokeTarget(null)}
      />
    </section>
  )
}

/**
 * 設定 →「ユーザー設定」の「共有リンク」(ADR-0029 7章)。自分が作った、取り消していない共有の
 * 一覧(起点のサムネイル、範囲、原本の可否、作成日時、最後に開かれた日時、開かれた回数)と、
 * リンクのコピー、取り消し(`ConfirmDialog` で確認)。管理者も他人の共有は見えない。
 * 機能が無効のあいだも一覧と取り消しはできる(無効である旨を添える)。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { ApiError, getShareSettings, listShares, revokeShare, type ShareRow } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import type { UseToastResult } from '../../components/Toast'
import { copyText } from '../../lib/copyText'
import { formatDateTime } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { shareScopeLabel } from '../share/shareScope'
import { SHARES_QUERY_KEY, SHARE_SETTINGS_QUERY_KEY } from './queryKeys'
import styles from './SharesSection.module.css'

interface SharesSectionProps {
  toast: UseToastResult
}

export function SharesSection({ toast }: SharesSectionProps) {
  const { t } = useI18n()
  const s = t.settings.shares
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: SHARES_QUERY_KEY, queryFn: listShares })
  const settingsQuery = useQuery({ queryKey: SHARE_SETTINGS_QUERY_KEY, queryFn: getShareSettings })
  const [revokeTarget, setRevokeTarget] = useState<ShareRow | null>(null)

  const revokeMutation = useMutation({
    mutationFn: revokeShare,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SHARES_QUERY_KEY })
      toast.show({ message: s.revokedToast })
    },
  })

  async function handleCopy(url: string) {
    const ok = await copyText(url)
    toast.show({ message: ok ? s.copiedToast : s.copyFailed })
  }

  const items = query.data?.items ?? []

  return (
    <section id="shares" className={styles.section}>
      <h2 className={styles.sectionHeading}>{s.heading}</h2>
      <p className={styles.helpText}>{s.intro}</p>
      {settingsQuery.data?.enabled === false && <p className={styles.warningText}>{s.disabledNote}</p>}

      {revokeMutation.isError && (
        <p className={styles.errorText}>
          {revokeMutation.error instanceof ApiError ? revokeMutation.error.message : s.revokeFailed}
        </p>
      )}

      {query.isLoading && <p className={styles.placeholder}>{s.loading}</p>}

      {query.isError && (
        <div className={styles.loadError}>
          <p className={styles.errorText}>{s.loadFailed}</p>
          <button type="button" className={styles.secondaryButton} onClick={() => void query.refetch()}>
            {s.retry}
          </button>
        </div>
      )}

      {query.data && items.length === 0 && <p className={styles.placeholder}>{s.empty}</p>}

      {items.length > 0 && (
        <ul className={styles.list}>
          {items.map((item) => (
            <li key={item.id} className={styles.item}>
              <Link
                to={`/assets/${item.root_asset_id}`}
                className={styles.thumbLink}
                aria-label={s.openRoot}
                title={item.root_title ?? s.openRoot}
              >
                <img
                  src={assetUrl(item.root_asset_id, 'thumb')}
                  alt=""
                  loading="lazy"
                  className={`${styles.thumb} checkerboard`}
                  data-deleted={item.root_deleted || undefined}
                />
              </Link>
              <div className={styles.itemText}>
                <span className={styles.itemName}>
                  {shareScopeLabel(item.scope)} · {fmt(s.imageCount, { count: item.asset_count })}
                </span>
                <code className={styles.url}>{item.url}</code>
                <dl className={styles.itemMeta}>
                  <dt>{s.original}</dt>
                  <dd>{item.allow_original ? s.originalAllowed : s.originalDenied}</dd>
                  <dt>{s.createdAt}</dt>
                  <dd>{formatDateTime(item.created_at)}</dd>
                  <dt>{s.lastAccessedAt}</dt>
                  <dd data-never={item.last_accessed_at ? undefined : 'true'}>
                    {item.last_accessed_at ? formatDateTime(item.last_accessed_at) : s.neverAccessed}
                  </dd>
                  <dt>{s.accessCount}</dt>
                  <dd>{item.access_count}</dd>
                </dl>
                {item.root_deleted && <p className={styles.warningText}>{s.rootDeleted}</p>}
              </div>
              <div className={styles.itemActions}>
                <button type="button" className={styles.secondaryButton} onClick={() => void handleCopy(item.url)}>
                  {s.copy}
                </button>
                <button
                  type="button"
                  className={styles.deleteButton}
                  disabled={revokeMutation.isPending}
                  onClick={() => setRevokeTarget(item)}
                >
                  {s.revoke}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        open={revokeTarget !== null}
        message={s.revokeConfirm.message}
        confirmLabel={s.revokeConfirm.confirmLabel}
        previewImageUrl={revokeTarget ? assetUrl(revokeTarget.root_asset_id, 'thumb') : undefined}
        previewDetail={revokeTarget ? shareScopeLabel(revokeTarget.scope) : undefined}
        onConfirm={() => {
          if (revokeTarget) revokeMutation.mutate(revokeTarget.id)
          setRevokeTarget(null)
        }}
        onCancel={() => setRevokeTarget(null)}
      />
    </section>
  )
}

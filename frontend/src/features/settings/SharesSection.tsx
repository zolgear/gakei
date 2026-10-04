/**
 * 設定の「共有リンク」ページ(`/settings/shares`。ADR-0029 7章、ADR-0031)。
 * 自分が作った、取り消していない共有の一覧(起点のサムネイル、範囲、原本の可否、作成日時、
 * 最後に開かれた日時、開かれた回数)と、リンクのコピー、取り消し(`ConfirmDialog` で確認)を
 * ページの本文にそのまま並べる。設定が1ページだった頃は、画面が縦に伸びないよう一覧を
 * ダイアログに出していたが、ページに分けたので本文に戻した(2026-10-01)。
 * サムネイルは遅延読み込みにする。
 * 管理者も他人の共有は見えない。
 * 管理者設定で無効のあいだは、このページごと出さない(`settingsPages.ts::settingsToc`)。
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { ApiError, listShares, revokeShare, type ShareRow } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import type { UseToastResult } from '../../components/Toast'
import { copyText } from '../../lib/copyText'
import { formatDateTime } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { shareScopeLabel } from '../share/shareScope'
import { SHARES_QUERY_KEY } from './queryKeys'
import { sharesCardState } from './sharesSummary'
import styles from './SharesSection.module.css'
import common from './settings.module.css'

interface SharesSectionProps {
  toast: UseToastResult
}

export function SharesSection({ toast }: SharesSectionProps) {
  const { t } = useI18n()
  const s = t.settings.shares
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: SHARES_QUERY_KEY, queryFn: listShares })
  const [revokeTarget, setRevokeTarget] = useState<ShareRow | null>(null)
  // 確認を閉じたあと、押した「取り消す」へフォーカスを戻すため(取り消して消えていれば戻さない)。
  const revokeTriggerRef = useRef<HTMLElement | null>(null)

  const items = query.data?.items ?? []
  const state = sharesCardState({
    isLoading: query.isLoading,
    isError: query.isError,
    itemCount: query.data ? items.length : undefined,
  })

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

  function openConfirm(item: ShareRow, trigger: HTMLElement) {
    revokeTriggerRef.current = trigger
    setRevokeTarget(item)
  }

  function closeConfirm() {
    setRevokeTarget(null)
    const trigger = revokeTriggerRef.current
    revokeTriggerRef.current = null
    requestAnimationFrame(() => {
      if (trigger?.isConnected) trigger.focus()
    })
  }

  return (
    <section id="shares" className={common.section}>
      {state === 'loading' && <p className={common.placeholder}>{s.loading}</p>}

      {state === 'error' && (
        <div className={common.loadError}>
          <p className={common.errorText}>{s.loadFailed}</p>
          <button type="button" className={common.secondaryButton} onClick={() => void query.refetch()}>
            {s.retry}
          </button>
        </div>
      )}

      {state === 'empty' && <p className={common.placeholder}>{s.empty}</p>}

      {revokeMutation.isError && (
        <p className={common.errorText}>
          {revokeMutation.error instanceof ApiError ? revokeMutation.error.message : s.revokeFailed}
        </p>
      )}

      {state === 'list' && (
        <>
          <p className={styles.count}>{fmt(s.count, { count: items.length })}</p>
          <ul className={common.list}>
            {items.map((item) => (
              <li key={item.id} className={`${common.item} ${styles.item}`}>
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
                <div className={common.itemText}>
                  <span className={common.itemName}>
                    {shareScopeLabel(item.scope)} · {fmt(s.imageCount, { count: item.asset_count })}
                  </span>
                  <code className={styles.url}>{item.url}</code>
                  <dl className={common.itemMeta}>
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
                  {item.root_deleted && <p className={common.warningText}>{s.rootDeleted}</p>}
                </div>
                <div className={styles.itemActions}>
                  <button type="button" className={common.secondaryButton} onClick={() => void handleCopy(item.url)}>
                    {s.copy}
                  </button>
                  <button
                    type="button"
                    className={common.dangerButton}
                    disabled={revokeMutation.isPending}
                    onClick={(e) => openConfirm(item, e.currentTarget)}
                  >
                    {s.revoke}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      <ConfirmDialog
        open={revokeTarget !== null}
        message={s.revokeConfirm.message}
        confirmLabel={s.revokeConfirm.confirmLabel}
        previewImageUrl={revokeTarget ? assetUrl(revokeTarget.root_asset_id, 'thumb') : undefined}
        previewDetail={revokeTarget ? shareScopeLabel(revokeTarget.scope) : undefined}
        onConfirm={() => {
          if (revokeTarget) revokeMutation.mutate(revokeTarget.id)
          closeConfirm()
        }}
        onCancel={closeConfirm}
      />
    </section>
  )
}

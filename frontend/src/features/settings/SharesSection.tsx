/**
 * 設定 →「ユーザー設定」の「共有リンク」(ADR-0029 7章)。カードには件数と「一覧を開く」だけを置き、
 * 自分が作った、取り消していない共有の一覧(起点のサムネイル、範囲、原本の可否、作成日時、
 * 最後に開かれた日時、開かれた回数)と、リンクのコピー、取り消し(`ConfirmDialog` で確認)は
 * ダイアログ(`Modal`)に出す。共有が増えても設定画面が縦に伸びないようにするため。
 * 管理者も他人の共有は見えない。
 * 管理者設定で無効のあいだは、このセクションごと出さない(`settingsSections.ts::visibleSections`)。
 */
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router'
import { ApiError, listShares, revokeShare, type ShareRow } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Modal } from '../../components/Modal'
import type { UseToastResult } from '../../components/Toast'
import { copyText } from '../../lib/copyText'
import { formatDateTime } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { shareScopeLabel } from '../share/shareScope'
import { SHARES_QUERY_KEY } from './queryKeys'
import { sharesCardState, sharesDialogCloseTarget } from './sharesSummary'
import styles from './SharesSection.module.css'

interface SharesSectionProps {
  toast: UseToastResult
}

export function SharesSection({ toast }: SharesSectionProps) {
  const { t } = useI18n()
  const s = t.settings.shares
  const query = useQuery({ queryKey: SHARES_QUERY_KEY, queryFn: listShares })
  const [dialogOpen, setDialogOpen] = useState(false)
  const openButtonRef = useRef<HTMLButtonElement>(null)

  const items = query.data?.items ?? []
  const state = sharesCardState({
    isLoading: query.isLoading,
    isError: query.isError,
    itemCount: query.data ? items.length : undefined,
  })

  function closeDialog() {
    setDialogOpen(false)
    // 開いたボタンへフォーカスを戻す(取り消して 0 件になり、ボタンが消えていれば何もしない)。
    requestAnimationFrame(() => openButtonRef.current?.focus())
  }

  return (
    <section id="shares" className={styles.section}>
      <h2 className={styles.sectionHeading}>{s.heading}</h2>
      <p className={styles.helpText}>{s.intro}</p>

      {state === 'loading' && <p className={styles.placeholder}>{s.loading}</p>}

      {state === 'error' && (
        <div className={styles.loadError}>
          <p className={styles.errorText}>{s.loadFailed}</p>
          <button type="button" className={styles.secondaryButton} onClick={() => void query.refetch()}>
            {s.retry}
          </button>
        </div>
      )}

      {state === 'empty' && <p className={styles.placeholder}>{s.empty}</p>}

      {state === 'list' && (
        <div className={styles.summaryRow}>
          <span className={styles.count}>{fmt(s.count, { count: items.length })}</span>
          <button
            ref={openButtonRef}
            type="button"
            className={styles.secondaryButton}
            onClick={() => setDialogOpen(true)}
          >
            {s.openList}
          </button>
        </div>
      )}

      <SharesListDialog open={dialogOpen} items={items} onClose={closeDialog} toast={toast} />
    </section>
  )
}

interface SharesListDialogProps {
  open: boolean
  items: ShareRow[]
  onClose: () => void
  toast: UseToastResult
}

/**
 * 共有の一覧ダイアログ。スクロールするのは `Modal` の本文だけ。
 * 取り消しの確認は一覧の上に重ねて出し、そのあいだの Esc は確認だけを閉じる
 * (`Modal` は Esc を window で受けて `onClose` を呼ぶので、閉じる先をここで振り分ける)。
 */
function SharesListDialog({ open, items, onClose, toast }: SharesListDialogProps) {
  const { t } = useI18n()
  const s = t.settings.shares
  const queryClient = useQueryClient()
  const [revokeTarget, setRevokeTarget] = useState<ShareRow | null>(null)
  // 確認を閉じたあと、押した「取り消す」へフォーカスを戻すため(取り消して消えていれば戻さない)。
  const revokeTriggerRef = useRef<HTMLElement | null>(null)

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

  function handleClose() {
    if (sharesDialogCloseTarget(revokeTarget !== null) === 'confirm') {
      closeConfirm()
      return
    }
    revokeMutation.reset()
    onClose()
  }

  return (
    <>
      <Modal open={open} title={s.dialogTitle} onClose={handleClose}>
        <div className={styles.dialogBody}>
          {revokeMutation.isError && (
            <p className={styles.errorText}>
              {revokeMutation.error instanceof ApiError ? revokeMutation.error.message : s.revokeFailed}
            </p>
          )}

          {items.length === 0 && <p className={styles.placeholder}>{s.empty}</p>}

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
                    <button
                      type="button"
                      className={styles.secondaryButton}
                      onClick={() => void handleCopy(item.url)}
                    >
                      {s.copy}
                    </button>
                    <button
                      type="button"
                      className={styles.deleteButton}
                      disabled={revokeMutation.isPending}
                      onClick={(e) => openConfirm(item, e.currentTarget)}
                    >
                      {s.revoke}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Modal>

      {/* Modal の外(兄弟)に置き、一覧ダイアログの上に重ねる(重なり順は ConfirmDialog.module.css)。 */}
      <ConfirmDialog
        open={open && revokeTarget !== null}
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
    </>
  )
}

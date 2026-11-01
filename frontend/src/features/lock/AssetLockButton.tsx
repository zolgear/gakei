/**
 * Asset 1枚を見せる場所(ビューア)のロックのボタン(ADR-0060)。錠前のアイコンで、付いていれば塗る。
 * 押すとダイアログを開く。付いていなければ種類(納品・共有・提出・その他)とメモを選んで付け、
 * 付いていれば中身を見せ、外せる人(付けた人と管理者)には「ロックを外す」を出す。
 * 付け外しの後は、その画像と一覧(ストック・履歴)のキャッシュを取り直す(祖先の守りも変わるため)。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, lockAsset, unlockAsset, type AssetLockKind, type AssetLockView } from '../../api/client'
import { LockIcon } from '../../components/icons'
import { Modal } from '../../components/Modal'
import { formatDateTime } from '../../lib/format'
import { useI18n } from '../../i18n'
import { LOCK_KINDS, useLockKindLabels } from './lockKinds'
import styles from './AssetLock.module.css'

interface AssetLockButtonProps {
  assetId: string
  lock: AssetLockView | null | undefined
  /** 置く場所のアイコンのボタンのクラス(枠と大きさ)。 */
  className?: string
  /** 削除済みの Asset には付け外しできない(API が 404 を返す)。 */
  disabled?: boolean
}

export function AssetLockButton({ assetId, lock, className, disabled = false }: AssetLockButtonProps) {
  const { t } = useI18n()
  const l = t.lock
  const [open, setOpen] = useState(false)
  const locked = Boolean(lock)
  const label = locked ? l.lockedLabel : l.lock
  return (
    <>
      <button
        type="button"
        className={className ? `${className} ${styles.button}` : styles.button}
        data-locked={locked}
        aria-pressed={locked}
        aria-label={label}
        title={label}
        aria-haspopup="dialog"
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        <LockIcon filled={locked} />
      </button>
      <Modal open={open} title={locked ? l.lockedTitle : l.lockTitle} onClose={() => setOpen(false)}>
        {open && <LockDialogBody assetId={assetId} lock={lock ?? null} onDone={() => setOpen(false)} />}
      </Modal>
    </>
  )
}

interface LockDialogBodyProps {
  assetId: string
  lock: AssetLockView | null
  onDone: () => void
}

function LockDialogBody({ assetId, lock, onDone }: LockDialogBodyProps) {
  const { t } = useI18n()
  const l = t.lock
  const kindLabels = useLockKindLabels()
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<AssetLockKind>('delivered')
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => (lock ? unlockAsset(assetId) : lockAsset(assetId, kind, note).then(() => undefined)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['asset', assetId] })
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.invalidateQueries({ queryKey: ['run'] })
      queryClient.invalidateQueries({ queryKey: ['history-assets'] })
      onDone()
    },
    onError: (err: unknown) => setError(err instanceof ApiError ? err.message : l.failed),
  })

  if (lock) {
    return (
      <div className={styles.form}>
        <dl className={styles.info}>
          <dt>{l.kind}</dt>
          <dd>{kindLabels[lock.kind]}</dd>
          {lock.note && (
            <>
              <dt>{l.note}</dt>
              <dd>{lock.note}</dd>
            </>
          )}
          <dt>{l.lockedAt}</dt>
          <dd>{formatDateTime(lock.locked_at)}</dd>
          {lock.locked_by_name && (
            <>
              <dt>{l.lockedBy}</dt>
              <dd>{lock.locked_by_name}</dd>
            </>
          )}
        </dl>
        {error && (
          <p className={styles.errorText} role="alert">
            {error}
          </p>
        )}
        <div className={styles.actions}>
          {lock.can_unlock && (
            <button
              type="button"
              className={styles.secondary}
              onClick={() => mutation.mutate()}
              disabled={mutation.isPending}
            >
              {l.unlock}
            </button>
          )}
          <button type="button" className={styles.secondary} onClick={onDone}>
            {l.close}
          </button>
        </div>
      </div>
    )
  }

  return (
    <form
      className={styles.form}
      onSubmit={(e) => {
        e.preventDefault()
        mutation.mutate()
      }}
    >
      <p className={styles.lead}>{l.lead}</p>
      <div className={styles.kinds} role="radiogroup" aria-label={l.kind}>
        {LOCK_KINDS.map((id) => (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={kind === id}
            className={styles.kind}
            onClick={() => setKind(id)}
          >
            {kindLabels[id]}
          </button>
        ))}
      </div>
      <textarea
        className={styles.note}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={l.notePlaceholder}
        aria-label={l.note}
        maxLength={2000}
      />
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <button type="submit" className={styles.primary} disabled={mutation.isPending}>
          {l.submit}
        </button>
        <button type="button" className={styles.secondary} onClick={onDone}>
          {l.cancel}
        </button>
      </div>
    </form>
  )
}

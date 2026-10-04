/**
 * ビューアの「共有」から開くダイアログ(ADR-0029 2章)。範囲(この1枚 / 祖先まで / 祖先と子孫)
 * と原本の可否(既定は許す)を選ぶと、含まれる画像の枚数とサムネイルを先に見せ(`previewShare`)、
 * 確かめてから作る。作ったあとはリンクとコピーボタンを出す。
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, createShare, previewShare, type ShareRow, type ShareScope } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { Modal } from '../../components/Modal'
import type { UseToastResult } from '../../components/Toast'
import { fmt, useI18n } from '../../i18n'
import { CopyableValue } from '../settings/CopyableValue'
import { SHARES_QUERY_KEY } from '../settings/queryKeys'
import { SHARE_SCOPES } from './shareScope'
import styles from './ShareDialog.module.css'

interface ShareDialogProps {
  open: boolean
  assetId: string
  onClose: () => void
  toast: UseToastResult
}

export function ShareDialog({ open, assetId, onClose, toast }: ShareDialogProps) {
  const { t } = useI18n()
  return (
    <Modal open={open} title={t.share.dialogTitle} onClose={onClose}>
      {/* 開くたびに作り直し、前回の選択や作成済みのリンクを残さない。 */}
      {open && <ShareDialogBody assetId={assetId} onClose={onClose} toast={toast} />}
    </Modal>
  )
}

function ShareDialogBody({ assetId, onClose, toast }: Omit<ShareDialogProps, 'open'>) {
  const { t } = useI18n()
  const s = t.share
  const queryClient = useQueryClient()
  const [scope, setScope] = useState<ShareScope>('single')
  const [allowOriginal, setAllowOriginal] = useState(true)
  const [created, setCreated] = useState<ShareRow | null>(null)

  const preview = useQuery({
    queryKey: ['share-preview', assetId, scope],
    queryFn: () => previewShare(assetId, scope),
    enabled: created === null,
  })

  const createMutation = useMutation({
    mutationFn: () => createShare({ asset_id: assetId, scope, allow_original: allowOriginal }),
    onSuccess: (row) => {
      setCreated(row)
      void queryClient.invalidateQueries({ queryKey: SHARES_QUERY_KEY })
    },
  })

  if (created) {
    return (
      <div className={styles.body}>
        <p className={styles.createdHeading}>{s.createdHeading}</p>
        <CopyableValue
          value={created.url}
          copyLabel={s.copy}
          copiedMessage={s.copiedToast}
          copyFailedMessage={s.copyFailed}
          toast={toast}
        />
        <p className={styles.helpText}>{s.createdNote}</p>
        <div className={styles.actions}>
          <button type="button" className={styles.secondaryButton} onClick={onClose}>
            {s.close}
          </button>
        </div>
      </div>
    )
  }

  const assets = preview.data?.assets ?? []

  return (
    <div className={styles.body}>
      <p className={styles.helpText}>{s.intro}</p>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>{s.scopeLabel}</legend>
        <div className={styles.scopeOptions}>
          {SHARE_SCOPES.map((value) => (
            <label key={value} className={styles.scopeOption} data-checked={scope === value}>
              <input
                type="radio"
                name="gakei-share-scope"
                value={value}
                checked={scope === value}
                onChange={() => setScope(value)}
              />
              <span className={styles.scopeName}>{s.scopes[value]}</span>
              <span className={styles.scopeHelp}>{s.scopeHelp[value]}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className={styles.checkboxRow}>
        <input
          type="checkbox"
          checked={allowOriginal}
          onChange={(e) => setAllowOriginal(e.target.checked)}
        />
        <span>{s.allowOriginalLabel}</span>
      </label>
      <p className={styles.helpText}>{s.allowOriginalHelp}</p>

      <div className={styles.previewBox}>
        {preview.isLoading && <p className={styles.placeholder}>{s.previewLoading}</p>}
        {preview.isError && (
          <p className={styles.errorText}>
            {preview.error instanceof ApiError ? preview.error.message : s.previewFailed}
          </p>
        )}
        {preview.data && (
          <>
            <p className={styles.previewCount}>
              {fmt(s.previewCount, { count: preview.data.asset_count })}
            </p>
            <ul className={styles.thumbs}>
              {assets.map((a) => (
                <li key={a.id} className={styles.thumb} title={a.title ?? undefined}>
                  <img
                    src={assetUrl(a.id, 'thumb')}
                    alt={a.title ?? ''}
                    loading="lazy"
                    className={`${styles.thumbImg} checkerboard`}
                  />
                </li>
              ))}
            </ul>
            {preview.data.truncated && <p className={styles.warningText}>{s.previewTruncated}</p>}
            {scope !== 'single' && <p className={styles.helpText}>{s.previewFixedNote}</p>}
          </>
        )}
      </div>

      {createMutation.isError && (
        <p className={styles.errorText}>
          {createMutation.error instanceof ApiError ? createMutation.error.message : s.createFailed}
        </p>
      )}

      <div className={styles.actions}>
        <button type="button" className={styles.secondaryButton} onClick={onClose}>
          {t.common.cancel}
        </button>
        <button
          type="button"
          className={styles.primaryButton}
          disabled={!preview.data || createMutation.isPending}
          onClick={() => createMutation.mutate()}
        >
          {createMutation.isPending ? s.creating : s.create}
        </button>
      </div>
    </div>
  )
}

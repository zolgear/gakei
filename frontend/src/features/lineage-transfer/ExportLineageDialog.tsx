/**
 * 「系列を書き出す」ダイアログ(ADR-0037 1章)。範囲(この画像と祖先 / 系列全体)を選ぶと、
 * 含まれる画像と Generated の数、原本の合計を先に見せ(`previewLineageExport`)、
 * ダウンロードのリンクで ZIP を受け取る。ビューアと系列グラフのインスペクターから開く。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ApiError,
  lineageExportUrl,
  previewLineageExport,
  type LineageExportScope,
} from '../../api/client'
import { Modal } from '../../components/Modal'
import type { UseToastResult } from '../../components/Toast'
import { formatBytes } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { LINEAGE_EXPORT_SCOPES } from './lineageTransfer'
import styles from './LineageTransfer.module.css'

interface ExportLineageDialogProps {
  open: boolean
  assetId: string
  onClose: () => void
  toast?: UseToastResult
}

export function ExportLineageDialog({ open, assetId, onClose, toast }: ExportLineageDialogProps) {
  const { t } = useI18n()
  return (
    <Modal open={open} title={t.lineageTransfer.export.dialogTitle} onClose={onClose}>
      {/* 開くたびに作り直し、前回の選択を残さない。 */}
      {open && <ExportLineageDialogBody assetId={assetId} onClose={onClose} toast={toast} />}
    </Modal>
  )
}

function ExportLineageDialogBody({ assetId, onClose, toast }: Omit<ExportLineageDialogProps, 'open'>) {
  const { t } = useI18n()
  const s = t.lineageTransfer.export
  const [scope, setScope] = useState<LineageExportScope>('ancestors')

  const preview = useQuery({
    queryKey: ['lineage-export-preview', assetId, scope],
    queryFn: () => previewLineageExport(assetId, scope),
  })
  const ready = preview.data !== undefined && !preview.isFetching

  return (
    <div className={styles.body}>
      <p className={styles.helpText}>{s.intro}</p>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>{s.scopeLabel}</legend>
        <div className={styles.scopeOptions}>
          {LINEAGE_EXPORT_SCOPES.map((value) => (
            <label key={value} className={styles.scopeOption} data-checked={scope === value}>
              <input
                type="radio"
                name="gakei-lineage-export-scope"
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
              {' · '}
              {fmt(s.previewRuns, { count: preview.data.run_count })}
              {' · '}
              {formatBytes(preview.data.total_bytes)}
            </p>
            {preview.data.truncated && <p className={styles.warningText}>{s.previewTruncated}</p>}
          </>
        )}
        <p className={styles.helpText}>{s.notIncluded}</p>
      </div>

      <div className={styles.actions}>
        <button type="button" className={styles.secondaryButton} onClick={onClose}>
          {t.common.cancel}
        </button>
        {ready ? (
          <a
            className={styles.primaryButton}
            href={lineageExportUrl(assetId, scope)}
            download
            onClick={() => {
              toast?.show({ message: s.startedToast })
              onClose()
            }}
          >
            {s.download}
          </a>
        ) : (
          <button type="button" className={styles.primaryButton} disabled>
            {s.download}
          </button>
        )}
      </div>
    </div>
  )
}

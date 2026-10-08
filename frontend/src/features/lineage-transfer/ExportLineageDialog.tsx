/**
 * 「系列を書き出す」ダイアログ(ADR-0037 1章・4章)。用途(GAKEI に取り込む / 納品用)、
 * 実行者の名前を含めるか(既定は含めない)、範囲(この画像と祖先 / 系列全体)を選ぶと、
 * 含まれる画像と Generated の数、原本の合計を先に見せ(`previewLineageExport`)、
 * ダウンロードのリンクで ZIP を受け取る。ビューアと系列グラフのインスペクターから開く。
 * 「系列をプレビュー」で、選んだ範囲の ZIP に入る画像と Generated を系列グラフで見せる
 * (`ExportPreviewGraph`。範囲を変えると描き直す。2026-10-07)。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ApiError,
  lineageExportUrl,
  previewLineageExport,
  type LineageExportMode,
  type LineageExportScope,
} from '../../api/client'
import { Modal } from '../../components/Modal'
import type { UseToastResult } from '../../components/Toast'
import { formatBytes } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { ExportPreviewGraph } from './ExportPreviewGraph'
import { LINEAGE_EXPORT_MODES, LINEAGE_EXPORT_SCOPES } from './lineageTransfer'
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
  const [mode, setMode] = useState<LineageExportMode>('import')
  const [scope, setScope] = useState<LineageExportScope>('ancestors')
  const [includeCreatorNames, setIncludeCreatorNames] = useState(false)
  const [showGraph, setShowGraph] = useState(false)

  const preview = useQuery({
    queryKey: ['lineage-export-preview', assetId, scope],
    queryFn: () => previewLineageExport(assetId, scope),
  })
  const ready = preview.data !== undefined && !preview.isFetching

  return (
    <div className={styles.body}>
      <p className={styles.helpText}>{s.intro}</p>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>{s.purposeLabel}</legend>
        <div className={styles.scopeOptions}>
          {LINEAGE_EXPORT_MODES.map((value) => (
            <label key={value} className={styles.scopeOption} data-checked={mode === value}>
              <input
                type="radio"
                name="gakei-lineage-export-mode"
                value={value}
                checked={mode === value}
                onChange={() => setMode(value)}
              />
              <span className={styles.scopeName}>{s.purposes[value]}</span>
              <span className={styles.scopeHelp}>{s.purposeHelp[value]}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>{s.scopeLabel}</legend>
        <div className={`${styles.scopeOptions} ${styles.scopeOptionsThree}`}>
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
        <div>
          <button
            type="button"
            className={styles.secondaryButton}
            aria-expanded={showGraph}
            onClick={() => setShowGraph((v) => !v)}
          >
            {showGraph ? s.hideGraph : s.showGraph}
          </button>
        </div>
        {showGraph && <ExportPreviewGraph assetId={assetId} scope={scope} />}
      </div>

      <label className={styles.checkOption}>
        <input
          type="checkbox"
          checked={includeCreatorNames}
          onChange={(event) => setIncludeCreatorNames(event.target.checked)}
        />
        <span className={styles.checkText}>
          <span className={styles.scopeName}>{s.includeCreatorNames}</span>
          <span className={styles.scopeHelp}>{s.includeCreatorNamesHelp}</span>
        </span>
      </label>

      <div className={styles.actions}>
        <button type="button" className={styles.secondaryButton} onClick={onClose}>
          {t.common.cancel}
        </button>
        {ready ? (
          <a
            className={styles.primaryButton}
            href={lineageExportUrl(assetId, { scope, mode, includeCreatorNames })}
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

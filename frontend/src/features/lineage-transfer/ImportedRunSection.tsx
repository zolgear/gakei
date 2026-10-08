/**
 * 取り込んだ Generated(`origin = 'import'`。ADR-0037)の、書き出し元での記録。元の実行者と日時を
 * 出し、ZIP の自己申告で検証していないことを書く。Run 詳細から使う。
 */
import type { RunImportInfo } from '../../api/client'
import { formatDateTime } from '../../lib/format'
import { useI18n } from '../../i18n'
import styles from './ImportedRunSection.module.css'

interface ImportedRunSectionProps {
  info: RunImportInfo
}

export function ImportedRunSection({ info }: ImportedRunSectionProps) {
  const { t } = useI18n()
  const s = t.lineageTransfer.importedRun
  return (
    <section className={styles.box}>
      <h2 className={styles.heading}>
        <span className={styles.badge}>{s.badge}</span>
        {s.heading}
      </h2>
      <p className={styles.note}>{s.unverifiedNote}</p>
      <dl className={styles.metaList}>
        <dt>{s.sourceCreator}</dt>
        <dd>{info.source_creator_name ?? s.unknown}</dd>
        <dt>{s.sourceCreatedAt}</dt>
        <dd>{info.source_created_at ? formatDateTime(info.source_created_at) : s.unknown}</dd>
        <dt>{s.sourceFinishedAt}</dt>
        <dd>{info.source_finished_at ? formatDateTime(info.source_finished_at) : s.unknown}</dd>
        <dt>{s.sourceVersion}</dt>
        <dd>{info.source_gakei_version ?? s.unknown}</dd>
        <dt>{s.importedAt}</dt>
        <dd>{formatDateTime(info.imported_at)}</dd>
        <dt>{s.sourceRunId}</dt>
        <dd className={styles.mono}>{info.source_run_id}</dd>
      </dl>
    </section>
  )
}

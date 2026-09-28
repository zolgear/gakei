/**
 * 履歴パネル・検索パネルで共有する「Run 1件」の行表示(コンパクト版)。
 * `HistoryCard` と違い、削除・キャンセル・再実行などの操作は持たない縦一列の参照用の行。
 * 左に先頭出力のサムネイル(無ければ状態を示すプレースホルダ)、右にプロンプトの抜粋と
 * メタ情報(非成功時は状態・日時・モデル名の順。狭い幅では末尾のモデル名から欠ける)を並べる。クリックすると、スタジオにいる間は
 * 結果エリアにその Run を表示し、それ以外では Run 詳細ページへ遷移する(`nodeTargetPath`)。
 */
import { useLocation, useNavigate } from 'react-router'
import type { RunSummary } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { nodeTargetPath } from '../lineage/nodeTargetPath'
import { formatDateTime, shortenModelName, statusLabel } from '../../lib/format'
import { useI18n } from '../../i18n'
import styles from './RunListRow.module.css'

interface RunListRowProps {
  run: RunSummary
  /** 既定は「この Run を表示」(historyPanel.openRun)。呼び出し側の文脈に合わせて上書き可。 */
  ariaLabel?: string
}

export function RunListRow({ run, ariaLabel }: RunListRowProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const firstOutput = run.outputs?.[0]

  return (
    <button
      type="button"
      className={styles.row}
      aria-label={ariaLabel ?? t.historyPanel.openRun}
      onClick={() => navigate(nodeTargetPath({ id: run.id, type: 'run' }, location.pathname))}
    >
      <span className={styles.thumbWrap}>
        {firstOutput ? (
          <img
            className={`${styles.thumb} checkerboard`}
            src={assetUrl(firstOutput.asset_id, 'thumb')}
            alt=""
            draggable={false}
          />
        ) : (
          <span className={styles.statusPlaceholder} data-status={run.status} aria-hidden="true" />
        )}
      </span>
      <span className={styles.body}>
        {/* 先頭出力のタイトル(ADR-0024)があれば、プロンプトの上に 1 行で出す。 */}
        {firstOutput?.title && <span className={styles.title}>{firstOutput.title}</span>}
        <span className={styles.prompt}>{run.prompt || t.history.card.noPrompt}</span>
        {/* 狭い幅では末尾から欠けるので、重要な順(状態 → 日時 → モデル)に並べる。モデルは
            履歴カードと同じく model_label(ComfyUI ならワークフロー名)を優先する。 */}
        <span className={styles.meta}>
          {run.status !== 'succeeded' && (
            <span className={styles.metaStatus} data-status={run.status}>
              {statusLabel(run.status)}
            </span>
          )}
          <span className={styles.metaDate}>{formatDateTime(run.queued_at)}</span>
          <span className={styles.metaModel}>{run.model_label ?? shortenModelName(run.model)}</span>
        </span>
      </span>
    </button>
  )
}

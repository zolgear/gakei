/**
 * 履歴のカードの画像部分。「詳細」のカード(`HistoryCard`)と「画像」の形(`HistoryTile`)で共通に使う。
 * 成功なら最初の出力のサムネイル(焦点に合わせて切り抜く。ドラッグで入力に足せる)と枚数のバッジ、
 * 失敗ならエラー、実行中・待機中・キャンセルならその表示を出す。押したときの行き先は
 * `resolveHistoryCardNavigation` の `mediaHref` を受け取る。
 * `compact` は「画像」の形の小さいマス向けで、エラーなどの文字を詰める。
 */
import { Link } from 'react-router'
import type { RunSummary } from '../../api/client'
import { GAKEI_ASSET_ID_DATA_TYPE } from '../run-form/dragDropAssets'
import { formatElapsedSeconds } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { focalStyle } from '../../lib/focalPoint'
import styles from './HistoryCard.module.css'

interface HistoryCardMediaProps {
  run: RunSummary
  href: string
  compact?: boolean
}

export function HistoryCardMedia({ run, href, compact = false }: HistoryCardMediaProps) {
  const { t } = useI18n()
  const outputs = run.outputs ?? []
  const firstOutput = outputs[0]
  const isPending = run.status === 'running' || run.status === 'queued'
  const ariaLabel = firstOutput
    ? t.history.card.showImage
    : isPending
      ? t.history.card.watchProgress
      : t.history.card.viewDetail
  const elapsed = run.status === 'running' ? formatElapsedSeconds(run.started_at) : null

  return (
    <Link to={href} className={styles.mediaLink} aria-label={ariaLabel} data-compact={compact}>
      {run.status === 'succeeded' && firstOutput && (
        <>
          <AssetThumb
            assetId={firstOutput.asset_id}
            rating={firstOutput.content_rating}
            withheld={firstOutput.content_withheld}
            badge
            // 左下は親のサムネイル、右上は出力の枚数なので左上に置く。
            badgePosition="top-left"
            className={`${styles.mediaImage} checkerboard`}
            style={focalStyle(firstOutput.focal_point)}
            alt=""
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, firstOutput.asset_id)
              e.dataTransfer.effectAllowed = 'copy'
            }}
          />
          {outputs.length > 1 && (
            <span className={styles.countBadge}>{fmt(t.history.card.outputCount, { count: outputs.length })}</span>
          )}
        </>
      )}

      {run.status === 'failed' && (
        <div className={styles.errorArea}>
          <span className={styles.errorCode}>{run.error_code ?? 'error'}</span>
          {run.error_message && <span className={styles.errorMessage}>{run.error_message}</span>}
        </div>
      )}

      {isPending && (
        <div className={styles.pendingArea}>
          <span className={styles.pendingText}>
            {run.status === 'running' ? (elapsed ?? t.history.card.running) : t.history.card.queued}
          </span>
          {run.status === 'running' && (
            <div className={styles.progressTrack}>
              <div className={styles.progressBar} />
            </div>
          )}
        </div>
      )}

      {run.status === 'canceled' && (
        <div className={styles.pendingArea}>
          <span className={styles.pendingText}>{t.history.card.canceled}</span>
        </div>
      )}
    </Link>
  )
}

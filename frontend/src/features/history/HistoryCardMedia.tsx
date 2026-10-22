/**
 * 履歴のカードの画像部分(`HistoryCard` の詳細と重ねる形で共通)。
 * 成功なら出力のサムネイル(焦点に合わせて切り抜く。ドラッグで入力に足せる)、失敗ならエラー、
 * 実行中・待機中・キャンセルならその表示を出す。押したときの行き先は
 * `resolveHistoryCardNavigation` の `mediaHref` を受け取る。
 *
 * - 詳細の形: 最初の出力を出し、複数なら右上に枚数のバッジ。レーティングの隅のバッジも出す。
 * - 重ねる形: 出力ごとにカードを分ける(`output` がそのカードの出力)ので、枚数の代わりに
 *   「1/2」のような何枚目かを出す。マスクしていない画像には隅のレーティングのバッジを出さない
 *   (マスクと、マスクに戻すボタンは AssetThumb のまま出る)。
 */
import { Link } from 'react-router'
import type { RunOutputRef, RunSummary } from '../../api/client'
import { GAKEI_ASSET_ID_DATA_TYPE } from '../run-form/dragDropAssets'
import { formatElapsedSeconds } from '../../lib/format'
import { fmt, useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import { focalStyle } from '../../lib/focalPoint'
import type { HistoryCardVariant } from './HistoryCard'
import styles from './HistoryCard.module.css'

interface HistoryCardMediaProps {
  run: RunSummary
  /** 見せる出力(無ければ出力の無い Run の表示)。 */
  output: RunOutputRef | undefined
  href: string
  variant: HistoryCardVariant
}

export function HistoryCardMedia({ run, output, href, variant }: HistoryCardMediaProps) {
  const { t } = useI18n()
  const outputs = run.outputs ?? []
  const isPending = run.status === 'running' || run.status === 'queued'
  const ariaLabel = output
    ? t.history.card.showImage
    : isPending
      ? t.history.card.watchProgress
      : t.history.card.viewDetail
  const elapsed = run.status === 'running' ? formatElapsedSeconds(run.started_at) : null
  const position = output ? outputs.findIndex((o) => o.asset_id === output.asset_id) + 1 : 0

  return (
    <Link to={href} className={styles.mediaLink} aria-label={ariaLabel}>
      {run.status === 'succeeded' && output && (
        <>
          <AssetThumb
            assetId={output.asset_id}
            rating={output.content_rating}
            withheld={output.content_withheld}
            badge={variant === 'detail'}
            maskBadgeAlign={variant === 'overlay' ? 'upper' : 'center'}
            // 左下は親のサムネイル、右上は出力の枚数なので左上に置く。
            badgePosition="top-left"
            className={`${styles.mediaImage} checkerboard`}
            style={focalStyle(output.focal_point)}
            alt=""
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, output.asset_id)
              e.dataTransfer.effectAllowed = 'copy'
            }}
          />
          {outputs.length > 1 &&
            (variant === 'overlay' ? (
              <span className={styles.countBadge}>{`${position}/${outputs.length}`}</span>
            ) : (
              <span className={styles.countBadge}>{fmt(t.history.card.outputCount, { count: outputs.length })}</span>
            ))}
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

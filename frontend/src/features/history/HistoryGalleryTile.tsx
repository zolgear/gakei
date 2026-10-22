/**
 * 履歴の「画像」の形(石積みのギャラリー)の1枚。画像を元の縦横比のまま列の幅いっぱいに出し、
 * 常に出すのは左下のピクセルの大きさだけ。マウスを載せたとき(またはキーボードで中に入ったとき)だけ、
 * 下からの薄いグラデーションにプロンプトの1行と小さな操作を出す。タッチ端末(hover: none)では
 * 出さず、タップでビューアへ移る。
 * 操作と行き先は詳細のカードと同じ(`useHistoryRunActions`、`resolveHistoryCardNavigation`)。
 */
import { Link } from 'react-router'
import type { RunSummary } from '../../api/client'
import { stripAppendedText } from '../regions/regions'
import { useI18n } from '../../i18n'
import { resolveHistoryCardNavigation } from './historyNavigation'
import { formatPixelSize, galleryTileAspect } from './galleryLayout'
import { HistoryCardMedia } from './HistoryCardMedia'
import { useHistoryRunActions } from './useHistoryRunActions'
import styles from './HistoryCard.module.css'

interface HistoryGalleryTileProps {
  run: RunSummary
  outputIndex: number
}

export function HistoryGalleryTile({ run, outputIndex }: HistoryGalleryTileProps) {
  const { t } = useI18n()
  const outputs = run.outputs ?? []
  const shownOutput = outputs[outputIndex] ?? outputs[0]
  const nav = resolveHistoryCardNavigation({
    runId: run.id,
    status: run.status,
    firstOutputAssetId: shownOutput?.asset_id ?? null,
    primaryParentAssetId: run.primary_parent_asset_id ?? null,
  })
  const runActions = useHistoryRunActions(run, shownOutput, nav)
  const pixelSize = run.status === 'succeeded' ? formatPixelSize(shownOutput) : null
  const shownPrompt = stripAppendedText(run.prompt, run.regions?.appended_text)
  const isPending = run.status === 'queued' || run.status === 'running'

  return (
    <article
      className={styles.galleryTile}
      style={{ aspectRatio: String(galleryTileAspect(run, shownOutput)) }}
      data-highlight={run.status === 'running'}
    >
      <HistoryCardMedia run={run} output={shownOutput} href={nav.mediaHref} variant="gallery" />
      {pixelSize && <span className={`${styles.pixelSize} ${styles.galleryPixelSize}`}>{pixelSize}</span>}
      <div className={styles.galleryHover}>
        <Link
          to={nav.runHref}
          className={styles.galleryPrompt}
          aria-label={isPending ? t.history.card.watchProgress : t.history.card.viewDetail}
        >
          {shownOutput?.title || shownPrompt || t.history.card.noPrompt}
        </Link>
        {runActions.actions}
        {runActions.errors}
      </div>
      {runActions.dialogs}
    </article>
  )
}

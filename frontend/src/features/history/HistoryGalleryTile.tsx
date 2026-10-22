/**
 * 履歴のギャラリー(石積み・行揃え)の1枚。画像を元の縦横比のまま出し(石積みは列の幅いっぱい、
 * 行揃えは配置で決まった大きさ)、
 * ふだんは画像だけを見せる。マウスを載せたとき(またはキーボードで中に入ったとき)だけ、下からの
 * 薄いグラデーションに1行の帯(左にピクセルの大きさ、右に小さな操作)を出す。出力にタイトルが
 * あればその上に1行で出す(プロンプトは出さない)。タッチ端末(hover: none)では何も重ねず、
 * タップでビューアへ移る。
 * 操作は幅に収めるため「詳細」「再実行」(待機中はキャンセル)「入力に使う」「削除」だけにし、
 * 「系列」は外す。幅が足りなければピクセルの大きさを省いて操作を優先する。
 * 操作と行き先は詳細のカードと同じ(`useHistoryRunActions`、`resolveHistoryCardNavigation`)。
 */
import { Link } from 'react-router'
import type { RunSummary } from '../../api/client'
import { resolveHistoryCardNavigation } from './historyNavigation'
import { formatPixelSize } from './galleryLayout'
import { HistoryCardMedia } from './HistoryCardMedia'
import { useHistoryRunActions } from './useHistoryRunActions'
import styles from './HistoryCard.module.css'

interface HistoryGalleryTileProps {
  run: RunSummary
  outputIndex: number
  /** 縦横比(`galleryTileAspect`)。石積みでは幅いっぱいにこの比で出す。 */
  aspect: number
  /** 行揃え(`layoutJustifiedRows`)で決まった大きさ(px)。無ければ幅いっぱいに縦横比で出す。 */
  size?: { width: number; height: number }
}

export function HistoryGalleryTile({ run, outputIndex, aspect, size }: HistoryGalleryTileProps) {
  const outputs = run.outputs ?? []
  const shownOutput = outputs[outputIndex] ?? outputs[0]
  const nav = resolveHistoryCardNavigation({
    runId: run.id,
    status: run.status,
    firstOutputAssetId: shownOutput?.asset_id ?? null,
    primaryParentAssetId: run.primary_parent_asset_id ?? null,
  })
  const pixelSize = run.status === 'succeeded' ? formatPixelSize(shownOutput) : null
  const runActions = useHistoryRunActions(run, shownOutput, nav, {
    showLineage: false,
    leading: pixelSize ? <span className={`${styles.pixelSize} ${styles.galleryPixelSize}`}>{pixelSize}</span> : null,
  })
  const title = shownOutput?.title

  return (
    <article
      className={styles.galleryTile}
      style={size ? { width: size.width, height: size.height } : { width: '100%', aspectRatio: String(aspect) }}
      data-highlight={run.status === 'running'}
    >
      <HistoryCardMedia run={run} output={shownOutput} href={nav.mediaHref} variant="gallery" />
      <div className={styles.galleryHover}>
        {title && (
          <Link to={nav.runHref} className={styles.galleryTitle} title={title}>
            {title}
          </Link>
        )}
        {runActions.actions}
        {runActions.errors}
      </div>
      {runActions.dialogs}
    </article>
  )
}

/**
 * 履歴の「画像」の形のマス。正方形に最初の出力の画像を敷き詰め、ヘッダー・プロンプト・チップ・
 * 操作は出さない(それらは「詳細」の `HistoryCard` と Run 詳細にある)。押したときの行き先は
 * 詳細のカードの画像部分と同じ。
 */
import type { RunSummary } from '../../api/client'
import { resolveHistoryCardNavigation } from './historyNavigation'
import { HistoryCardMedia } from './HistoryCardMedia'
import styles from './HistoryTile.module.css'

interface HistoryTileProps {
  run: RunSummary
}

export function HistoryTile({ run }: HistoryTileProps) {
  const nav = resolveHistoryCardNavigation({
    runId: run.id,
    status: run.status,
    firstOutputAssetId: run.outputs?.[0]?.asset_id ?? null,
    primaryParentAssetId: run.primary_parent_asset_id ?? null,
  })
  return (
    <article className={styles.tile} data-highlight={run.status === 'running'}>
      <HistoryCardMedia run={run} href={nav.mediaHref} compact />
    </article>
  )
}

/**
 * サイドバーの「系列」パネル(ナビゲーター)。起点は LineageOriginContext(ビューア/Run詳細/
 * 履歴カードの「系列を見る」で更新される)。ノードクリックは直接遷移する(Asset→ビューア、
 * Run→Run詳細)。ただしスタジオにいる間はページを離れず、結果エリアに表示する
 * (`LineageGraph` の既定の遷移、`nodeTargetPath.ts`。2026-09-26)。
 *
 * 「起点を固定(ピン留め)」: 既定 ON。固定中はページ遷移のたびにグラフが再構築・再配置されず、
 * 起点は動かないまま、今見ている Asset/Run をグラフ上で「現在地」として強調するだけにする。
 * 固定を外すと、常に表示中の Asset を起点にする従来の挙動に戻る。固定中でも、現在地がグラフに
 * 全く含まれていない(遠くへ移動した)場合だけ自動で起点を選び直す。
 *
 * 状態の追随は effect ではなく、レンダー中に「ズレていたらその場で補正する」書き方にしている
 * (`SearchPage` の URL 同期と同じ考え方。React 公式が薦める「props から派生した state を
 * リセットする」パターン)。
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { getAssetLineage } from '../../api/client'
import { useLineageOrigin } from '../../context/useLineageOrigin'
import { useI18n } from '../../i18n'
import { deriveCurrentViewNode } from './currentViewNode'
import { LineageGraph } from './LineageGraph'
import styles from './LineageGraphPanel.module.css'

interface PinState {
  pinned: boolean
  pinnedOriginId: string | null
}

export function LineageGraphPanel() {
  const { t } = useI18n()
  const { originAssetId } = useLineageOrigin()
  const navigate = useNavigate()
  const location = useLocation()
  const [pin, setPin] = useState<PinState>(() => ({ pinned: true, pinnedOriginId: originAssetId }))

  // 固定していない間は、表示中の Asset に常に追随させる(従来どおりの挙動)。
  if (!pin.pinned && pin.pinnedOriginId !== originAssetId) {
    setPin({ pinned: false, pinnedOriginId: originAssetId })
  }

  const effectiveOriginId = pin.pinned ? (pin.pinnedOriginId ?? originAssetId) : originAssetId
  const currentViewNode = deriveCurrentViewNode(location.pathname, originAssetId)

  const lineageQuery = useQuery({
    queryKey: ['lineage', effectiveOriginId],
    queryFn: () => getAssetLineage(effectiveOriginId as string),
    enabled: effectiveOriginId !== null,
  })

  // 固定中でも、現在地がグラフに存在しなければ(=全く別の系列へ移動した)強制的に選び直す。
  if (
    pin.pinned &&
    currentViewNode &&
    lineageQuery.data &&
    originAssetId &&
    originAssetId !== effectiveOriginId
  ) {
    const present = (lineageQuery.data.nodes ?? []).some((n) => n.id === currentViewNode.id)
    if (!present) {
      setPin({ pinned: true, pinnedOriginId: originAssetId })
    }
  }

  return (
    <div className={styles.panel}>
      <div className={styles.headerRow}>
        <h2 className={styles.heading}>{t.lineage.heading}</h2>
        <button
          type="button"
          className={styles.pinButton}
          aria-pressed={pin.pinned}
          title={pin.pinned ? t.lineage.pinnedTitle : t.lineage.unpinnedTitle}
          onClick={() => setPin({ pinned: !pin.pinned, pinnedOriginId: originAssetId })}
        >
          {pin.pinned ? t.lineage.pinnedLabel : t.lineage.pinLabel}
        </button>
      </div>

      {effectiveOriginId && (
        <div className={styles.originRow}>
          <span className={styles.originId}>{effectiveOriginId.slice(0, 8)}</span>
          {pin.pinned && originAssetId && (
            <button
              type="button"
              className={styles.resyncButton}
              onClick={() => setPin({ pinned: true, pinnedOriginId: originAssetId })}
            >
              {t.lineage.resyncButton}
            </button>
          )}
        </div>
      )}

      {!effectiveOriginId && (
        <p className={styles.placeholder}>
          {t.lineage.emptyPlaceholder}
        </p>
      )}

      {effectiveOriginId && (
        <LineageGraph
          assetId={effectiveOriginId}
          height={340}
          compact
          onExpand={() => navigate(`/lineage/${effectiveOriginId}`)}
          highlightedNodeId={currentViewNode?.id ?? null}
        />
      )}

      <p className={styles.hint}>
        {t.lineage.hint}
      </p>
    </div>
  )
}

/**
 * `show_assets` の結果のカード(ADR-0048 10章)。アシスタントが見つけた画像をサムネイルの格子で
 * 見せる。Run のカードと同じく、サムネイルを選ぶとスタジオの結果エリアに出し(`/studio?asset=`)、
 * ドラッグすると生成フォームの入力に使える。「すべて添付」で次の発言の添付に入れる(最大4枚。
 * 入りきらない分と添付済みの分は、その旨を出す)。
 */
import { useState } from 'react'
import { useNavigate } from 'react-router'
import type { AssistantMessageResponse } from '../../api/client'
import { assetUrl } from '../../api/assetUrl'
import { fmt, useI18n } from '../../i18n'
import { focalStyle } from '../../lib/focalPoint'
import { GAKEI_ASSET_ID_DATA_TYPE } from '../run-form/dragDropAssets'
import { buildStudioPath } from '../workspace/assetQueryParam'
import { ASSISTANT_ATTACHMENTS_MAX, type AttachmentPlan } from './assistantForm'
import { readShownAssets } from './timeline'
import styles from './AssistantPanel.module.css'

interface AssetsCardProps {
  message: AssistantMessageResponse
  /** 次の発言の添付に足す。足した結果を返す。 */
  onAttach: (assetIds: string[]) => AttachmentPlan
}

export function AssetsCard({ message, onAttach }: AssetsCardProps) {
  const { t } = useI18n()
  const c = t.assistant.assetsCard
  const navigate = useNavigate()
  const shown = readShownAssets(message)
  const [notice, setNotice] = useState<string | null>(null)

  function attachAll() {
    const plan = onAttach(shown.assets.map((a) => a.assetId))
    const parts: string[] = []
    if (plan.added > 0) parts.push(fmt(c.attached, { count: plan.added }))
    if (plan.overflow > 0) parts.push(fmt(c.overflow, { count: plan.overflow, max: ASSISTANT_ATTACHMENTS_MAX }))
    if (plan.added === 0 && plan.overflow === 0) parts.push(c.alreadyAttached)
    setNotice(parts.join(' '))
  }

  return (
    <div className={styles.card} data-kind="assets">
      <div className={styles.cardHeader}>
        <span className={styles.cardKind}>{fmt(c.heading, { count: shown.assets.length })}</span>
        {shown.title && <span className={styles.cardTitle}>{shown.title}</span>}
      </div>
      <ul className={styles.assetGrid}>
        {shown.assets.map((asset) => (
          <li key={asset.assetId} className={styles.assetGridItem}>
            <button
              type="button"
              className={styles.thumbButton}
              aria-label={asset.title ? `${c.show}: ${asset.title}` : c.show}
              title={asset.title || c.show}
              onClick={() => navigate(buildStudioPath(asset.assetId))}
            >
              <img
                className={`${styles.thumb} checkerboard`}
                src={assetUrl(asset.assetId, 'thumb')}
                style={focalStyle(asset.focalPoint)}
                alt=""
                loading="lazy"
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(GAKEI_ASSET_ID_DATA_TYPE, asset.assetId)
                  e.dataTransfer.effectAllowed = 'copy'
                }}
              />
            </button>
          </li>
        ))}
      </ul>
      <div className={styles.cardActions}>
        <button type="button" className={styles.smallButton} onClick={attachAll}>
          {c.attachAll}
        </button>
        <span className={styles.cardNote}>{c.dragHint}</span>
      </div>
      {notice && (
        <p className={styles.cardNote} role="status">
          {notice}
        </p>
      )}
    </div>
  )
}

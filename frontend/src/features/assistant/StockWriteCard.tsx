/**
 * ストックの整理の結果のカード(ADR-0048 13章)。操作、対象(タグ・グループ・名前)、枚数、
 * サムネイルの一部、「元に戻す」を出す。サムネイルを選ぶとスタジオの結果エリアに出す(`show_assets`
 * のカードと同じ)。戻すのは書き込んだ直後から変わっていない画像だけで、戻した枚数と飛ばした枚数を
 * 出す。戻したらメッセージを置き換え、ストックとグループ・画像の詳細を取り直す。
 */
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { ApiError, undoAssistantWrite, type AssistantMessageResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { focalStyle } from '../../lib/focalPoint'
import { AssetThumb } from '../content-rating/AssetThumb'
import { setAssetDragData } from '../run-form/dragDropAssets'
import { buildStudioPath } from '../workspace/assetQueryParam'
import { describeStockUndo, invalidateStockQueries, readStockWrite } from './stockWrite'
import { useAssistantSession } from './useAssistantSession'
import styles from './AssistantPanel.module.css'

export function StockWriteCard({ message }: { message: AssistantMessageResponse }) {
  const { t } = useI18n()
  const c = t.assistant.stockCard
  const w = t.assistant.writeCard
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { replaceMessage } = useAssistantSession()
  const [error, setError] = useState<string | null>(null)
  const summary = readStockWrite(message)

  const undoMutation = useMutation({
    mutationFn: () => undoAssistantWrite(message.id),
    onSuccess: (response) => {
      setError(null)
      replaceMessage(response.message)
      invalidateStockQueries(queryClient)
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : w.undoFailed)
    },
  })

  if (!summary) return null
  const imageCount = summary.changed > 0 ? summary.changed : summary.count
  const hidden = Math.max(0, imageCount - summary.preview.length)
  const showCount = summary.tool !== 'rename_group' && summary.tool !== 'set_title'
  const undoResult = message.undo_result ?? null

  return (
    <div className={styles.card} data-kind="stock" data-undone={message.undone_at ? true : undefined}>
      <div className={styles.cardHeader}>
        <span className={styles.cardKind}>{c.titles[summary.tool]}</span>
        {summary.group && summary.tool !== 'rename_group' && (
          <span className={styles.cardTitle}>{summary.group.name}</span>
        )}
        {showCount && <span className={styles.cardNote}>{fmt(c.count, { count: imageCount })}</span>}
      </div>

      {summary.tags.length > 0 && (
        <ul className={styles.stockTags}>
          {summary.tags.map((tag) => (
            <li key={tag} className={styles.stockTag} data-removed={summary.tool === 'remove_tags' ? true : undefined}>
              {tag}
            </li>
          ))}
        </ul>
      )}

      {summary.before !== null && (
        <p className={styles.stockChange}>
          <span className={styles.beforeValue}>{summary.before || w.empty}</span>
          <span aria-hidden="true">→</span>
          <span className={styles.stockAfter}>{summary.after || w.empty}</span>
        </p>
      )}

      {summary.preview.length > 0 && (
        <ul className={styles.stockThumbs}>
          {summary.preview.map((asset) => (
            <li key={asset.assetId} className={styles.assetGridItem}>
              <button
                type="button"
                className={styles.thumbButton}
                aria-label={t.assistant.assetsCard.show}
                title={t.assistant.assetsCard.show}
                onClick={() => navigate(buildStudioPath(asset.assetId))}
              >
                <AssetThumb
                  assetId={asset.assetId}
                  rating={asset.contentRating}
                  className={`${styles.thumb} checkerboard`}
                  style={focalStyle(asset.focalPoint)}
                  alt=""
                  loading="lazy"
                  draggable
                  onDragStart={(e) => setAssetDragData(e.dataTransfer, asset.assetId)}
                />
              </button>
            </li>
          ))}
          {hidden > 0 && (
            <li className={styles.stockMore} aria-label={fmt(c.more, { count: hidden })}>
              {fmt(c.more, { count: hidden })}
            </li>
          )}
        </ul>
      )}

      <div className={styles.cardActions}>
        {message.undone_at ? (
          <span className={styles.cardNotice} role="status">
            {undoResult ? describeStockUndo(t, summary.tool, undoResult) : c.undoneSingle}
          </span>
        ) : message.undoable ? (
          <button
            type="button"
            className={styles.smallButton}
            disabled={undoMutation.isPending}
            onClick={() => undoMutation.mutate()}
          >
            {undoMutation.isPending ? w.undoing : summary.tool === 'create_group' ? c.undoCreate : w.undo}
          </button>
        ) : null}
      </div>
      {error && (
        <p className={styles.errorText} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

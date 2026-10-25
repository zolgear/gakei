/**
 * 画像を LLM に送る前の許可のカード(ADR-0048 14.1)。送る画像のサムネイルと送り先の接続先の名前を出す。
 *
 * - 答えを待っているとき(`pending`): 「1度だけ許可」「常に許可」「許可しない」のボタン。押したら答えを送り、
 *   道具の結果が届くまでボタンを止める。
 * - 答えた後・会話を開き直したとき(`message`): ボタンは出さず、tool のメッセージに残った答えを出す。
 */
import { useState } from 'react'
import type {
  AssistantImagePermissionView,
  AssistantMessageResponse,
  AssistantPermissionAnswer,
  ContentRatingLevel,
} from '../../api/client'
import { useI18n } from '../../i18n'
import { AssetThumb } from '../content-rating/AssetThumb'
import type { PendingPermission } from './assistantSession'
import { useAssistantSession } from './useAssistantSession'
import styles from './AssistantPanel.module.css'

type PermissionCardProps = { pending: PendingPermission; message?: undefined } | { message: AssistantMessageResponse; pending?: undefined }

const ANSWERS: AssistantPermissionAnswer[] = ['once', 'always', 'deny']

export function PermissionCard(props: PermissionCardProps) {
  const { t } = useI18n()
  const c = t.assistant.permissionCard
  const { answerPermission } = useAssistantSession()
  const [failed, setFailed] = useState(false)

  const saved: AssistantImagePermissionView | null = props.message?.image_permission ?? null
  const assetIds = props.pending ? props.pending.assetIds : (saved?.asset_ids ?? [])
  const ratings: Record<string, ContentRatingLevel> = props.pending
    ? props.pending.contentRatings
    : (saved?.content_ratings ?? {})
  const connectionName = props.pending ? props.pending.connectionName : (saved?.connection_name ?? '')

  let answeredText: string | null = null
  if (saved) {
    answeredText = saved.answer === 'deny' && saved.reason !== 'user' ? c.answered[saved.reason] : c.answered[saved.answer]
  }

  function answer(value: AssistantPermissionAnswer) {
    if (!props.pending) return
    setFailed(false)
    answerPermission(props.pending.requestId, value).catch(() => setFailed(true))
  }

  const sent = props.pending?.answer ?? null

  return (
    <div className={styles.card} data-kind="permission" data-answered={saved ? saved.answer : undefined}>
      <div className={styles.cardHeader}>
        <span className={styles.cardKind}>{c.heading}</span>
        <span className={styles.cardTitle}>{connectionName}</span>
      </div>
      <ul className={styles.permissionThumbs}>
        {assetIds.map((assetId) => (
          <li key={assetId}>
            <AssetThumb
              assetId={assetId}
              rating={ratings[assetId] ?? null}
              className={`${styles.attachmentThumb} checkerboard`}
              alt=""
              loading="lazy"
            />
          </li>
        ))}
      </ul>
      {props.pending ? (
        <div className={styles.cardActions} role="group" aria-label={c.heading}>
          {ANSWERS.map((value) => (
            <button
              key={value}
              type="button"
              className={value === 'once' ? `${styles.smallButton} ${styles.smallPrimary}` : styles.smallButton}
              disabled={sent !== null}
              aria-pressed={sent === value}
              onClick={() => answer(value)}
            >
              {c[value]}
            </button>
          ))}
          {sent !== null && (
            <span className={styles.cardNote} role="status">
              {c.sending}
            </span>
          )}
        </div>
      ) : (
        answeredText && (
          <p className={saved?.answer === 'deny' ? styles.cardNote : styles.cardNotice} role="status">
            {answeredText}
          </p>
        )
      )}
      {failed && (
        <p className={styles.errorText} role="alert">
          {c.failed}
        </p>
      )}
    </div>
  )
}

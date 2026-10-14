/**
 * `propose_prompt` の結果のカード(ADR-0048 2章)。プロンプトを見せ、「フォームに入れる(末尾に
 * 追加)」「フォームを置き換える」「コピー」を付ける。フォームへの反映は RunFormContext の
 * `requestPromptInsert`(スタジオが消費する)。置き換えるときに入力済みのプロンプトがあれば、
 * 確認する(`usePromptLoader` と同じ)。スタジオの外にいれば、スタジオへ移る。
 */
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
import type { AssistantMessageResponse } from '../../api/client'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useRunFormContext } from '../../context/useRunFormContext'
import { useI18n } from '../../i18n'
import { copyText } from '../../lib/copyText'
import { isStudioPath } from '../lineage/nodeTargetPath'
import { shouldConfirmReplace } from '../run-form/promptInsertion'
import styles from './AssistantPanel.module.css'

export function PromptCard({ message }: { message: AssistantMessageResponse }) {
  const { t } = useI18n()
  const c = t.assistant.promptCard
  const location = useLocation()
  const navigate = useNavigate()
  const { formState, requestPromptInsert } = useRunFormContext()
  const [confirming, setConfirming] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const result = message.result ?? {}
  const prompt = typeof result.prompt === 'string' ? result.prompt : ''
  const label = typeof result.label === 'string' ? result.label : ''
  const note = typeof result.note === 'string' ? result.note : ''

  function goStudio() {
    if (!isStudioPath(location.pathname)) navigate('/studio')
  }

  function insert() {
    requestPromptInsert(prompt, 'insert')
    goStudio()
    setNotice(c.inserted)
  }

  function replace() {
    requestPromptInsert(prompt, 'replace')
    goStudio()
    setNotice(c.replaced)
  }

  async function copy() {
    setNotice((await copyText(prompt)) ? c.copied : c.copyFailed)
  }

  return (
    <div className={styles.card} data-kind="prompt">
      <div className={styles.cardHeader}>
        <span className={styles.cardKind}>{c.heading}</span>
        {label && <span className={styles.cardTitle}>{label}</span>}
      </div>
      <pre className={styles.promptText}>{prompt}</pre>
      {note && <p className={styles.cardNote}>{note}</p>}
      <div className={styles.cardActions}>
        <button type="button" className={`${styles.smallButton} ${styles.smallPrimary}`} onClick={insert}>
          {c.insert}
        </button>
        <button
          type="button"
          className={styles.smallButton}
          onClick={() => (shouldConfirmReplace(formState.prompt) ? setConfirming(true) : replace())}
        >
          {c.replace}
        </button>
        <button type="button" className={styles.smallButton} onClick={() => void copy()}>
          {c.copy}
        </button>
      </div>
      {notice && (
        <p className={styles.cardNotice} role="status">
          {notice}
        </p>
      )}
      <ConfirmDialog
        open={confirming}
        message={c.replaceConfirm}
        confirmLabel={c.replaceConfirmLabel}
        onConfirm={() => {
          setConfirming(false)
          replace()
        }}
        onCancel={() => setConfirming(false)}
      />
    </div>
  )
}

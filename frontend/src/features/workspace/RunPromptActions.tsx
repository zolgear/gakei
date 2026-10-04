/**
 * 系列インスペクター(スタジオ)の Run プロンプト直下に出す「プロンプトに挿入/置き換え」。
 *
 * 下段の textarea は React の制御コンポーネント(`value={prompt}`、state は `InputPane` が
 * `useRunFormLogic` で持つローカル state)なので、DOM(`id="prompt"`)を直接書き換えても
 * 次の描画で元に戻ってしまう。そのため文字列の書き込みは必ず `insertPrompt`(`InputPane` から
 * 受け渡される実体、実際に `setPrompt` を呼ぶ)経由で行う。カーソル位置の「読み取り」だけは
 * ここで DOM から行う(フォーカスの有無・selectionStart は state ではないため)。
 *
 * 挿入・置き換えはアイコンだけのボタン(名前は aria-label と title)。ブラウザのダイアログは
 * 使わない。置き換えの確認はボタン横の2段階確認(「置き換える?」→「はい」)で、ここは文字のまま。
 */
import { useState } from 'react'
import { InsertTextIcon, ReplaceIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import type { InsertPromptFn } from '../run-form/promptInsertion'
import { shouldConfirmReplace } from '../run-form/promptInsertion'
import styles from './RunPromptActions.module.css'

export interface RunPromptActionsProps {
  /** インスペクターに表示している Run のプロンプト全文(挿入/置き換えの中身)。 */
  prompt: string
  /** 下段 textarea の現在の内容(置き換え確認が要るかどうかの判定用)。 */
  currentPrompt: string
  insertPrompt: InsertPromptFn
}

function readCursorPos(): number | null {
  const el = document.getElementById('prompt')
  const hasFocus = el instanceof HTMLTextAreaElement && document.activeElement === el
  return hasFocus ? el.selectionStart : null
}

export function RunPromptActions({ prompt, currentPrompt, insertPrompt }: RunPromptActionsProps) {
  const { t } = useI18n()
  const rpa = t.workspace.runPromptActions
  const [confirmingReplace, setConfirmingReplace] = useState(false)

  function handleInsert() {
    insertPrompt(prompt, 'insert', readCursorPos())
    setConfirmingReplace(false)
  }

  function applyReplace() {
    insertPrompt(prompt, 'replace', readCursorPos())
    setConfirmingReplace(false)
  }

  function handleReplaceClick() {
    if (shouldConfirmReplace(currentPrompt)) {
      setConfirmingReplace(true)
    } else {
      applyReplace()
    }
  }

  return (
    <div className={styles.row}>
      <button
        type="button"
        className={styles.iconButton}
        aria-label={rpa.insertIntoPrompt}
        title={rpa.insertIntoPrompt}
        onClick={handleInsert}
      >
        <InsertTextIcon />
      </button>
      {!confirmingReplace ? (
        <button
          type="button"
          className={styles.iconButton}
          aria-label={rpa.replacePrompt}
          title={rpa.replacePrompt}
          onClick={handleReplaceClick}
        >
          <ReplaceIcon />
        </button>
      ) : (
        <span className={styles.confirmGroup}>
          <span className={styles.confirmLabel}>{rpa.confirmReplace}</span>
          <button type="button" className={styles.confirmYes} onClick={applyReplace}>
            {rpa.yes}
          </button>
          <button
            type="button"
            className={styles.confirmCancel}
            onClick={() => setConfirmingReplace(false)}
          >
            {rpa.cancel}
          </button>
        </span>
      )}
    </div>
  )
}

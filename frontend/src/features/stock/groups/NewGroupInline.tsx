/**
 * 「+」(新しいグループ)と、押すと出る名前の入力(ADR-0022 4章)。ストックの「グループなし」の
 * 見出し(`GroupSection`)と生成フォームの「グループ」(`AssetGroupField`)で見た目と操作を
 * 揃えるために共有する。ボタンと入力欄は置き場所が離れる(見出し行の中と、その下)ので、
 * 状態は `useNewGroupInline`(別ファイル)にまとめ、表示部品を `NewGroupButton` / `NewGroupNameInput`
 * の 2 つに分ける。
 *
 * - 「+」: ラベルなしのアイコン。`aria-label` とツールチップは `stock.groups.newButton`。
 *   開いている間は `aria-expanded` が真で、もう一度押すと取り消す。
 * - 入力: Enter で作成(IME の変換確定の Enter は無視)、Escape で取り消し。失敗は下に文言。
 */
import { useI18n } from '../../../i18n'
import type { NewGroupInlineState } from './useNewGroupInline'
import styles from './NewGroupInline.module.css'

interface NewGroupButtonProps {
  state: NewGroupInlineState
  /** 大きさと配置は置き場所ごとに違うので呼び出し側が持つ(色・ホバーも呼び出し側の CSS)。 */
  className: string
}

export function NewGroupButton({ state, className }: NewGroupButtonProps) {
  const { t } = useI18n()
  return (
    <button
      type="button"
      className={className}
      aria-label={t.stock.groups.newButton}
      title={t.stock.groups.newButton}
      aria-expanded={state.creating}
      onClick={state.toggle}
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <path d="M7 2v10M2 7h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    </button>
  )
}

interface NewGroupNameInputProps {
  state: NewGroupInlineState
  /** 入力欄に足すクラス(狭い幅でのタップ対象の高さなど)。 */
  inputClassName?: string
  /**
   * Enter / Escape を外側のキー操作(生成フォームの Ctrl+Enter の送信など)に渡さない。
   * ストックの見出しでは従来どおり渡す(既定)。
   */
  stopKeyPropagation?: boolean
}

/** 開いているときだけ描く。閉じていれば何も出さない。 */
export function NewGroupNameInput({ state, inputClassName, stopKeyPropagation = false }: NewGroupNameInputProps) {
  const { t } = useI18n()
  if (!state.creating) return null
  return (
    <div className={styles.createGroup}>
      <input
        autoFocus
        className={inputClassName ? `${styles.nameInput} ${inputClassName}` : styles.nameInput}
        value={state.draft}
        maxLength={100}
        placeholder={t.stock.groups.newPlaceholder}
        aria-label={t.stock.groups.newPlaceholder}
        disabled={state.isPending}
        onChange={(e) => state.setDraft(e.target.value)}
        onKeyDown={(e) => {
          // IME の変換確定の Enter では作成しない。
          if (e.nativeEvent.isComposing) return
          if (e.key === 'Enter') {
            e.preventDefault()
            if (stopKeyPropagation) e.stopPropagation()
            state.submit()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            if (stopKeyPropagation) e.stopPropagation()
            state.cancel()
          }
        }}
      />
      {state.error && <p className={styles.error}>{state.error}</p>}
    </div>
  )
}

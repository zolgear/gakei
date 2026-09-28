/**
 * 等幅で値を1つ見せ、横に「コピー」ボタンを置く小さな部品(ADR-0023 6章)。MCP の接続先 URL・
 * 登録コマンドの例と、発行直後のアクセストークンの表示に使う。値は選択しやすいよう `<code>` に
 * 入れ、長い値は折り返す(狭い幅で横にはみ出さない)。コピーの成否はトーストで知らせる。
 */
import type { UseToastResult } from '../../components/Toast'
import { copyText } from '../../lib/copyText'
import styles from './CopyableValue.module.css'

interface CopyableValueProps {
  value: string
  copyLabel: string
  copiedMessage: string
  copyFailedMessage: string
  toast: UseToastResult
  /** 値の見出し(`<label>` 相当)と結び付ける id。 */
  labelledBy?: string
}

export function CopyableValue({ value, copyLabel, copiedMessage, copyFailedMessage, toast, labelledBy }: CopyableValueProps) {
  async function handleCopy() {
    const ok = await copyText(value)
    toast.show({ message: ok ? copiedMessage : copyFailedMessage })
  }

  return (
    <div className={styles.row}>
      <code className={styles.value} aria-labelledby={labelledBy}>
        {value}
      </code>
      <button type="button" className={styles.copyButton} onClick={() => void handleCopy()}>
        {copyLabel}
      </button>
    </div>
  )
}

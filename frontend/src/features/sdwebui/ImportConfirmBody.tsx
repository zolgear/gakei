/**
 * 画像の生成情報で今のプロンプトを置き換える前の確認(ADR-0038 9章、11章)。`Modal` の本文として使う
 * (スタジオの「画像から設定を読み込む」とビューアの「SD WebUI のフォームに読み込む」で共通)。
 * 読み込むモデルとプロンプトを見せてから置き換える。
 */
import { fmt, useI18n } from '../../i18n'
import styles from './ImportParams.module.css'

interface ImportConfirmBodyProps {
  /** 読み込むモデルの名前。見つからない(モデルを変えない)ときは null。 */
  model: string | null
  prompt: string
  onConfirm: () => void
  onCancel: () => void
}

export function ImportConfirmBody({ model, prompt, onConfirm, onCancel }: ImportConfirmBodyProps) {
  const { t } = useI18n()
  const ip = t.sdwebui.importParams
  return (
    <div className={styles.confirm}>
      <p className={styles.lead}>{ip.confirmMessage}</p>
      <p className={styles.previewMeta}>
        {model ? fmt(ip.previewModel, { model }) : ip.previewModelUnchanged}
      </p>
      {prompt && <p className={styles.previewPrompt}>{prompt}</p>}
      <div className={styles.actions}>
        <button type="button" className={styles.primary} onClick={onConfirm}>
          {ip.confirmReplace}
        </button>
        <button type="button" className={styles.secondary} onClick={onCancel}>
          {t.common.cancel}
        </button>
      </div>
    </div>
  )
}

/**
 * パラメーターセットで今のプロンプトを置き換える前の確認(ADR-0040 4章。画像からの読み込みの
 * `ImportConfirmBody` と同じ形)。`Modal` の本文として使う。
 */
import type { ParameterSetResponse } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import styles from '../sdwebui/ImportParams.module.css'

interface ParameterSetConfirmBodyProps {
  set: ParameterSetResponse
  onConfirm: () => void
  onCancel: () => void
}

export function ParameterSetConfirmBody({ set, onConfirm, onCancel }: ParameterSetConfirmBodyProps) {
  const { t } = useI18n()
  const l = t.parameterSets.load
  return (
    <div className={styles.confirm}>
      <p className={styles.lead}>{fmt(l.confirmMessage, { name: set.name })}</p>
      {set.prompt && <p className={styles.previewPrompt}>{set.prompt}</p>}
      <div className={styles.actions}>
        <button type="button" className={styles.primary} onClick={onConfirm}>
          {l.confirmReplace}
        </button>
        <button type="button" className={styles.secondary} onClick={onCancel}>
          {t.common.cancel}
        </button>
      </div>
    </div>
  )
}

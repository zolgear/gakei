/**
 * 下段配置(既定、ADR-0009 1章・2026-09-22 承認分)の並べ方。`InputPane` が組み立てた
 * スロット(ReactNode)を受けて、チップ行 → 2列グリッド(左: プロンプト、右: 設定)を
 * 現行の DOM のまま組み立てるだけで、状態・ハンドラは持たない。サイドバー配置
 * (`InputPaneSidebarLayout`、予定)も同じ `InputPaneSlots` を受ける。
 */
import type { ReactNode } from 'react'
import styles from './InputPane.module.css'

/** `InputPane` が組み立てた各部分の受け渡し口。配置(bottom/sidebar)ごとの並べ方が使う。 */
export interface InputPaneSlots {
  inputImages: ReactNode
  promptEditor: ReactNode
  promptTools: ReactNode
  submitControls: ReactNode
  notices: ReactNode
  modelField: ReactNode
  paramFields: ReactNode
  otherParams: ReactNode
}

export function InputPaneBottomLayout({
  inputImages,
  promptEditor,
  promptTools,
  submitControls,
  notices,
  modelField,
  paramFields,
  otherParams,
}: InputPaneSlots) {
  return (
    <>
      {inputImages}

      <div className={styles.grid}>
        <div className={styles.promptColumn}>
          {promptEditor}
          <div className={styles.promptActionsRow}>
            {promptTools}
            <span className={styles.actionsSpacer} />
            {submitControls}
          </div>
          {notices}
        </div>

        <div className={styles.settingsColumn}>
          <div className={styles.settingsGrid}>
            {modelField}
            {paramFields}
            {otherParams}
          </div>
        </div>
      </div>
    </>
  )
}

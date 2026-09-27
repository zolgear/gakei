/**
 * サイドバー配置(左列。ADR-0009 1章・2026-09-26 追記)の並べ方。`InputPane` が組み立てた
 * スロット(`InputPaneSlots`、`InputPaneBottomLayout` と共有)を受け、モデル・グループ → 入力画像 →
 * プロンプト → サイズ・主なパラメーター → その他(畳む)の順に縦へ並べ、参考価格と生成ボタン
 * (`submitControls`)は列の下端(`.footer`)に固定する。列自体が縦にスクロールし
 * (`.scroll`)、`InputPane.module.css` 側で `.pane[data-layout='sidebar']` の
 * padding/gap を 0 にして代わりにこの列が余白を持つ。状態・ハンドラは持たない。
 */
import type { InputPaneSlots } from './InputPaneBottomLayout'
import styles from './InputPaneSidebarLayout.module.css'

export function InputPaneSidebarLayout({
  inputImages,
  promptEditor,
  promptTools,
  submitControls,
  notices,
  modelField,
  paramFields,
  groupField,
  otherParams,
}: InputPaneSlots) {
  return (
    <div className={styles.column}>
      <div className={styles.scroll}>
        <div className={`${styles.section} ${styles.modelGroup}`}>
          {modelField}
          {groupField}
        </div>
        <div className={styles.section}>{inputImages}</div>
        <div className={`${styles.section} ${styles.prompt}`}>
          {promptEditor}
          <div className={styles.promptTools}>{promptTools}</div>
        </div>
        <div className={`${styles.section} ${styles.params}`}>
          {paramFields}
        </div>
        {otherParams}
      </div>

      <div className={styles.footer}>
        {notices}
        {submitControls}
      </div>
    </div>
  )
}

/**
 * プロンプト欄の下の小さな操作列。「テキスト / タグ」の切り替え(ADR-0039)と、プロンプトセット関連(保存・文字数)。セットの
 * 読み込みは `@` メンション(ADR-0009 2章)で行うので、専用のボタンは置かない(2026-09-26 に削除)。
 * SD WebUI では「LoRA」(ADR-0038 8章)も置く。参考価格と生成ボタンは `SubmitControls` 側。フラグメントなので、並べ方(区切り・余白)は
 * 呼び出し側(`InputPaneBottomLayout` など)が決める。
 */
import type { ReactNode } from 'react'
import { SaveToPromptSetButton } from '../prompt-sets/SaveToPromptSetButton'
import styles from './InputPane.module.css'

interface PromptToolsRowProps {
  /** `SaveToPromptSetButton` に渡す現在のプロンプト本文。 */
  promptText: string
  promptLength: number
  promptMax: number
  /** 「テキスト / タグ」の切り替え(先頭に置く)。 */
  editModeToggle?: ReactNode
  /** プロバイダー固有の補助(SD WebUI の「LoRA」。ADR-0038 8章)。切り替えの直後に置く。 */
  providerTools?: ReactNode
}

export function PromptToolsRow({
  promptText,
  promptLength,
  promptMax,
  editModeToggle,
  providerTools,
}: PromptToolsRowProps) {
  return (
    <>
      {editModeToggle}
      {providerTools}
      <SaveToPromptSetButton text={promptText} />
      <span className={styles.charCount}>
        {promptLength} / {promptMax}
      </span>
    </>
  )
}

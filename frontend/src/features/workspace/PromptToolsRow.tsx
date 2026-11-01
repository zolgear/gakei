/**
 * プロンプト欄の下の小さな操作列。「テキスト / タグ」の切り替え(ADR-0039)、プロバイダー固有の補助
 * (SD WebUI の「LoRA」。ADR-0038 8章)、プロンプトセットに保存、設定のメニュー(パラメーターセットの保存・
 * 読み込みと画像から読み込む。`FormSettingsMenu`)。保存と設定はアイコンレールと同じアイコンのボタンにする。
 * セットの読み込みは `@` メンション(ADR-0009 2章)で行うので、専用のボタンは置かない(2026-09-26 に削除)。
 * 文字数はプロンプト欄の右下(`InputPane`)。参考価格と生成ボタンは `SubmitControls` 側。フラグメントなので、
 * 並べ方(区切り・余白)は呼び出し側(`InputPaneBottomLayout` など)が決める。
 */
import type { ReactNode } from 'react'
import { PromptSetIcon } from '../../components/icons'
import { SaveToPromptSetButton } from '../prompt-sets/SaveToPromptSetButton'
import styles from './PromptToolsRow.module.css'

interface PromptToolsRowProps {
  /** `SaveToPromptSetButton` に渡す現在のプロンプト本文。 */
  promptText: string
  /** 「テキスト / タグ」の切り替え(先頭に置く)。 */
  editModeToggle?: ReactNode
  /** プロバイダー固有の補助(SD WebUI の「LoRA」。ADR-0038 8章)。切り替えの直後に置く。 */
  providerTools?: ReactNode
  /** 設定のメニュー(`FormSettingsMenu`)。最後に置く。 */
  settingsMenu?: ReactNode
}

export function PromptToolsRow({ promptText, editModeToggle, providerTools, settingsMenu }: PromptToolsRowProps) {
  return (
    <>
      {editModeToggle}
      {providerTools}
      <SaveToPromptSetButton text={promptText} icon={<PromptSetIcon size={16} />} iconClassName={styles.iconTrigger} />
      {settingsMenu}
    </>
  )
}

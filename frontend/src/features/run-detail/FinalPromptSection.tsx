/**
 * 最終プロンプト(PE の出力。ADR-0030 3章)の表示。見出し「PE の出力」にノードのラベルを小さく
 * 添え、本文は数行だけ見せて折り畳む(改行は保つ)。「コピー」は常に出し、挿入・置き換えなどの
 * 操作は置き場所ごとに `renderActions` で差し込む(共有リンクのページは渡さない=閲覧専用)。
 *
 * ビューア、Run の詳細、スタジオの系列インスペクター、共有リンクのページから使う。共有リンクの
 * ページは RunFormProvider の外で描かれるので、ここではフォームの context に触れない。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { RunTextOutput } from '../../api/client'
import { copyText } from '../../lib/copyText'
import { useI18n } from '../../i18n'
import { findFinalPrompt, finalPromptNodeLabel, isFinalPromptCollapsible } from './finalPrompt'
import styles from './FinalPromptSection.module.css'

const COPY_FEEDBACK_MS = 2000

interface FinalPromptSectionProps {
  textOutputs: RunTextOutput[] | null | undefined
  /** 見出しの要素(周りの見出しの階層に合わせる)。 */
  headingLevel?: 'h2' | 'h3'
  /** 見出しのクラス(周りの見出しと見た目を揃える)。 */
  headingClassName?: string
  /** コピーの隣に並べる操作(挿入・置き換え)。最終プロンプトの全文を受け取る。 */
  renderActions?: (text: string) => ReactNode
  className?: string
}

export function FinalPromptSection({
  textOutputs,
  headingLevel = 'h3',
  headingClassName,
  renderActions,
  className,
}: FinalPromptSectionProps) {
  const { t } = useI18n()
  const fp = t.finalPrompt
  const [expanded, setExpanded] = useState(false)
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    [],
  )

  const item = findFinalPrompt(textOutputs)
  if (!item) return null

  const nodeLabel = finalPromptNodeLabel(item)
  const collapsible = isFinalPromptCollapsible(item.text)
  const collapsed = collapsible && !expanded
  const Heading = headingLevel

  async function handleCopy(text: string) {
    const ok = await copyText(text)
    setCopyState(ok ? 'copied' : 'failed')
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setCopyState('idle'), COPY_FEEDBACK_MS)
  }

  return (
    <div className={className ? `${styles.section} ${className}` : styles.section}>
      <div className={styles.headingRow}>
        <Heading className={headingClassName ?? styles.heading}>{fp.heading}</Heading>
        {nodeLabel && <span className={styles.nodeLabel}>{nodeLabel}</span>}
      </div>
      <p className={styles.text} data-collapsed={collapsed || undefined}>
        {item.text}
      </p>
      {item.truncated && <p className={styles.note}>{fp.truncated}</p>}
      {collapsible && (
        <button
          type="button"
          className={styles.linkButton}
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? fp.collapse : fp.expand}
        </button>
      )}
      <div className={styles.toolbar}>
        {renderActions?.(item.text)}
        <button type="button" className={styles.copyButton} onClick={() => void handleCopy(item.text)}>
          {copyState === 'copied' ? fp.copied : copyState === 'failed' ? fp.copyFailed : fp.copy}
        </button>
      </div>
    </div>
  )
}

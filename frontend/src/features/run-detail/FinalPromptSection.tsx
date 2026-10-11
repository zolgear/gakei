/**
 * 最終プロンプト(ADR-0030 3章)と、出力ごとの展開後のプロンプト(ADR-0038 7章)の表示。
 * 見出し「最終プロンプト」にノードのラベルを小さく添え、本文は数行だけ見せて折り畳む(改行は保つ)。
 * 「コピー」(アイコン)は常に出し、挿入・置き換えなどの操作は置き場所ごとに `renderActions` で
 * 差し込む(共有リンクのページは渡さない=閲覧専用)。ネガティブプロンプトには挿入・置き換えを出さない。
 *
 * `outputIndex` を渡すと、その出力(Asset)に当たるものだけを出す(ビューア、系列のインスペクター、
 * 共有リンクの画像)。渡さないと Run 全体(Run の詳細)で、Run のプロンプトと違う展開後のプロンプトを
 * 出力ごとに並べる。展開後のプロンプトは `prompt`(と `negativePrompt`)と同じなら出さない。
 *
 * ビューア、Run の詳細、スタジオの系列インスペクター、共有リンクのページから使う。共有リンクの
 * ページは RunFormProvider の外で描かれるので、ここではフォームの context に触れない。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { RunTextOutput } from '../../api/client'
import { CheckIcon, CopyIcon, CrossIcon } from '../../components/icons'
import { copyText } from '../../lib/copyText'
import { fmt, useI18n } from '../../i18n'
import {
  finalPromptNodeLabel,
  isFinalPromptCollapsible,
  selectFinalPromptEntries,
  type FinalPromptEntry,
  type FinalPromptSelection,
} from './finalPrompt'
import styles from './FinalPromptSection.module.css'

const COPY_FEEDBACK_MS = 2000

interface FinalPromptSectionProps extends FinalPromptSelection {
  /** 見出しの要素(周りの見出しの階層に合わせる)。 */
  headingLevel?: 'h2' | 'h3'
  /** 見出しのクラス(周りの見出しと見た目を揃える)。 */
  headingClassName?: string
  /** コピーの隣に並べる操作(挿入・置き換え)。プロンプトの全文を受け取る。 */
  renderActions?: (text: string) => ReactNode
  className?: string
}

export function FinalPromptSection(props: FinalPromptSectionProps) {
  const { t } = useI18n()
  const fp = t.finalPrompt
  const { outputIndex, headingLevel = 'h3', headingClassName, renderActions, className } = props
  const entries = selectFinalPromptEntries(props)
  if (entries.length === 0) return null

  const Heading = headingLevel
  const headingClass = headingClassName ?? styles.heading
  const runWide = outputIndex === undefined
  const shared = runWide ? entries.filter((e) => e.outputIndex === null) : entries
  // Run 全体では、出力ごとのものを見出し「展開後のプロンプト」1つの下に、出力の番号を添えて並べる。
  const perOutput = runWide ? entries.filter((e) => e.outputIndex !== null) : []

  function headingFor(entry: FinalPromptEntry): string {
    if (entry.kind === 'final') return fp.heading
    return entry.kind === 'expanded' ? fp.expandedHeading : fp.expandedNegativeHeading
  }

  function actionsFor(entry: FinalPromptEntry) {
    return entry.kind === 'expandedNegative' ? undefined : renderActions
  }

  return (
    <div className={className ? `${styles.group} ${className}` : styles.group}>
      {shared.map((entry) => (
        <FinalPromptBlock
          key={`${entry.kind}:${entry.outputIndex ?? 'all'}`}
          item={entry.item}
          heading={<Heading className={headingClass}>{headingFor(entry)}</Heading>}
          label={entry.kind === 'final' ? finalPromptNodeLabel(entry.item) : null}
          renderActions={actionsFor(entry)}
        />
      ))}
      {perOutput.length > 0 && (
        <div className={styles.perOutput}>
          <Heading className={headingClass}>{fp.expandedHeading}</Heading>
          {perOutput.map((entry) => (
            <FinalPromptBlock
              key={`${entry.kind}:${entry.outputIndex}`}
              item={entry.item}
              label={fmt(entry.kind === 'expandedNegative' ? fp.outputNegativeLabel : fp.outputLabel, {
                // Run の詳細の出力の一覧(#0, #1, …)と同じ番号にする。
                index: entry.outputIndex ?? 0,
              })}
              renderActions={actionsFor(entry)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface FinalPromptBlockProps {
  item: RunTextOutput
  /** 見出し(Run 全体の出力ごとの一覧では省き、`label` だけを出す)。 */
  heading?: ReactNode
  /** 見出しに小さく添えるラベル(ノードのラベル、出力の番号)。 */
  label: string | null
  renderActions?: (text: string) => ReactNode
}

/** 1件分(見出し、本文、折り畳み、コピーと操作)。折り畳みとコピーの状態は1件ごとに持つ。 */
function FinalPromptBlock({ item, heading, label, renderActions }: FinalPromptBlockProps) {
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

  const collapsible = isFinalPromptCollapsible(item.text)
  const collapsed = collapsible && !expanded
  const copyLabel = copyState === 'copied' ? fp.copied : copyState === 'failed' ? fp.copyFailed : fp.copy

  async function handleCopy(text: string) {
    const ok = await copyText(text)
    setCopyState(ok ? 'copied' : 'failed')
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setCopyState('idle'), COPY_FEEDBACK_MS)
  }

  return (
    <div className={styles.section}>
      {(heading || label) && (
        <div className={styles.headingRow}>
          {heading}
          {label && <span className={styles.nodeLabel}>{label}</span>}
        </div>
      )}
      {/* 折り畳みの line-clamp は内側の要素に掛ける(枠の padding に次の行が覗かないように)。 */}
      <div className={styles.textBox}>
        <p className={styles.text} data-collapsed={collapsed || undefined}>
          {item.text}
        </p>
      </div>
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
        {/* アイコンだけのボタン。コピーの結果はアイコンと名前を一時的に変えて伝える。 */}
        <button
          type="button"
          className={styles.iconButton}
          aria-label={copyLabel}
          title={copyLabel}
          data-state={copyState}
          onClick={() => void handleCopy(item.text)}
        >
          {copyState === 'copied' ? <CheckIcon /> : copyState === 'failed' ? <CrossIcon /> : <CopyIcon />}
        </button>
        {/* 名前の変化は読み上げられないことがあるので、結果は別に aria-live で知らせる。 */}
        <span className={styles.srOnly} aria-live="polite">
          {copyState === 'idle' ? '' : copyLabel}
        </span>
      </div>
    </div>
  )
}

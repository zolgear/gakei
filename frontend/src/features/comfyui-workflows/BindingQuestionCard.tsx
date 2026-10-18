/**
 * 問いのカード(ADR-0052 4章)。読み取りで決めきれなかった項目(`ask`)だけに出す。
 * 候補を同じ順の欄(既定値の冒頭、ノード、タイトル、位置、道筋)で見比べて選ぶ。
 * 「どちらでもない」は行内のピッカーへつなぐ。画像の枠の問いは順を確かめるだけ。
 */
import { useState } from 'react'
import type { ComfyBindingCandidate, ComfyNodeInfo } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { nodeDisplayTitle, questionCardId, valuePreview, type PendingQuestion } from './bindingCards'
import { ConfidenceBadge } from './BindingRows'
import { NodeIdChip, NodeName, NodePath } from './NodeName'
import styles from './BindingCards.module.css'

interface BindingQuestionCardProps {
  question: PendingQuestion
  nodes: readonly ComfyNodeInfo[]
  onAnswer: (candidate: ComfyBindingCandidate | null) => void
  onNeither: () => void
}

function candidateValueHead(node: ComfyNodeInfo | undefined, candidate: ComfyBindingCandidate): string | null {
  if (!candidate.input) return null
  return valuePreview(node?.inputs?.find((i) => i.name === candidate.input)?.value, 80)
}

function sizeSummary(node: ComfyNodeInfo | undefined): string | null {
  const get = (name: string) => node?.inputs?.find((i) => i.name === name)
  const parts = ['width', 'height', 'batch_size'].map((name) => {
    const input = get(name)
    if (!input) return '-'
    return input.linked ? '↔' : String(input.value)
  })
  return parts.every((p) => p === '-') ? null : parts.join(' × ')
}

export function BindingQuestionCard({ question, nodes, onAnswer, onNeither }: BindingQuestionCardProps) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const [selected, setSelected] = useState(0)
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const candidates = question.reading.candidates ?? []
  const isOrder = question.id === 'images'
  const headingId = `question-${question.id}`

  return (
    <section id={questionCardId(question.id)} className={styles.question} aria-labelledby={headingId} tabIndex={-1}>
      <div className={styles.questionHead}>
        <ConfidenceBadge badge="ask" />
        <h3 id={headingId} className={styles.questionTitle}>
          {question.reading.question ?? c.questionFallback}
        </h3>
      </div>
      <div className={styles.questionBody}>
        <p className={styles.why}>{question.reading.reason}</p>
        {!isOrder && question.id !== 'size' && <p className={styles.why}>{c.questionOthersNote}</p>}
        <div className={styles.cands} role={isOrder ? 'list' : 'radiogroup'} aria-labelledby={headingId}>
          {candidates.map((cand, i) => {
            const node = byId.get(cand.node)
            const head = question.id === 'size' ? sizeSummary(node) : candidateValueHead(node, cand)
            const title = nodeDisplayTitle(node)
            const content = (
              <>
                <span className={styles.candTop}>
                  <span className={styles.candKey}>
                    {isOrder ? fmt(c.slotNo, { index: i + 1 }) : fmt(c.candidateNo, { index: i + 1 })}
                  </span>
                  <NodeIdChip id={cand.node} />
                </span>
                {head !== null && <span className={styles.candQuote}>{head === '' ? c.emptyValue : head}</span>}
                <dl className={styles.candFacts}>
                  <dt>{c.factNode}</dt>
                  <dd>
                    <NodeName id={cand.node} node={node} input={cand.input} />
                  </dd>
                  <dt>{c.factTitle}</dt>
                  <dd>{title ?? c.noTitle}</dd>
                  <dt>{c.factPosition}</dt>
                  <dd>
                    {node ? c.states[node.state] : '-'}
                    {cand.path && cand.path.length > 1 ? ` · ${fmt(c.pathDepth, { count: cand.path.length - 1 })}` : ''}
                  </dd>
                </dl>
                {cand.path && cand.path.length > 0 && <NodePath steps={cand.path} />}
              </>
            )
            return isOrder ? (
              <div key={`${cand.node}.${cand.input}`} className={styles.cand} role="listitem">
                {content}
              </div>
            ) : (
              <button
                key={`${cand.node}.${cand.input}`}
                type="button"
                role="radio"
                aria-checked={selected === i}
                className={styles.cand}
                onClick={() => setSelected(i)}
              >
                {content}
              </button>
            )
          })}
        </div>
        <div className={styles.questionFoot}>
          <button
            type="button"
            className={styles.primarySmall}
            disabled={!isOrder && candidates[selected] === undefined}
            onClick={() => onAnswer(isOrder ? null : (candidates[selected] ?? null))}
          >
            {isOrder ? c.answerKeepOrder : c.answerChoose}
          </button>
          <button type="button" className={styles.linkButton} onClick={onNeither}>
            {isOrder ? c.answerReorder : c.answerNeither}
          </button>
        </div>
      </div>
    </section>
  )
}

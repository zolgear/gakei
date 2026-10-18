/**
 * ノードの名前の見せ方(ADR-0052 4章)。タイトルを主に、クラス名を等幅で小さく、id は短いチップ
 * (サブグラフの部分は短くし、完全な id はツールチップ)。文字列の入力には既定値の冒頭を添える。
 */
import type { ComfyNodeInfo, ComfyPathStep } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { nodeDisplayTitle, shortNodeId, valuePreview } from './bindingCards'
import styles from './BindingCards.module.css'

export function NodeIdChip({ id }: { id: string }) {
  const { t } = useI18n()
  return (
    <span className={styles.nodeId} title={fmt(t.comfyui.cards.nodeIdTitle, { id })}>
      {shortNodeId(id)}
    </span>
  )
}

interface NodeNameProps {
  id: string
  node: ComfyNodeInfo | undefined
  /** 入力名(`.value` の形で添える)。ノードそのものを指すときは省く。 */
  input?: string | null
  /** 既定値の冒頭を添えるか(文字列の入力だけ)。数値は「既定値 N」。 */
  showValue?: boolean
  /** 既定値の冒頭の長さ。 */
  previewLength?: number
}

export function NodeName({ id, node, input, showValue = false, previewLength = 42 }: NodeNameProps) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const title = nodeDisplayTitle(node)
  const value = input ? node?.inputs?.find((i) => i.name === input)?.value : undefined
  const preview = showValue ? valuePreview(value, previewLength) : null
  return (
    <span className={styles.nodeNameBlock}>
      <span className={styles.nodeName}>
        {title ? (
          <>
            <span className={styles.nodeTitle}>{title}</span>
            <span className={styles.nodeClass}>{node?.class_type ?? '?'}</span>
          </>
        ) : (
          <span className={styles.nodeTitle}>{node?.class_type ?? c.unknownNode}</span>
        )}
        {input && <span className={styles.inputName}>.{input}</span>}
        <NodeIdChip id={id} />
      </span>
      {showValue && preview !== null && (
        <span className={styles.valuePreview}>
          {preview === '' ? c.emptyValue : <q>{preview}</q>}
        </span>
      )}
      {showValue && preview === null && (typeof value === 'number' || typeof value === 'boolean') && (
        <span className={styles.valuePreview}>{fmt(c.defaultValue, { value: String(value) })}</span>
      )}
    </span>
  )
}

/** 道筋(クラス名.入力名 のチップを矢印でつなぐ)。起点は金の枠、終点は破線。 */
export function NodePath({ steps }: { steps: readonly ComfyPathStep[] }) {
  const { t } = useI18n()
  if (steps.length === 0) return null
  return (
    <ol className={styles.path} aria-label={t.comfyui.cards.pathLabel}>
      {steps.map((step, index) => (
        <li key={`${step.node}-${index}`}>
          <span
            className={styles.pathStep}
            data-root={index === 0 ? 'true' : undefined}
            data-end={index === steps.length - 1 && index > 0 ? 'true' : undefined}
            title={fmt(t.comfyui.cards.nodeIdTitle, { id: step.node })}
          >
            {step.class_type ?? step.node}
            {step.input && <span className={styles.pathInput}>.{step.input}</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}

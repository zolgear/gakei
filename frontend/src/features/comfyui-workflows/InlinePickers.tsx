/**
 * 差し込み先の行の中で入力・ノードを選び直すピッカー(ADR-0052 4章)。
 * 候補(読み取りの候補、名前の合う入力)を先に、ほかの入力を後に並べ、絞り込める。
 * 配線された入力は出すが選べない。使われない枝・表示だけのノードには印を付ける。
 */
import { useId, useState } from 'react'
import type { ComfyBindingCandidate, ComfyNodeInfo } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { nodePickerOptions, pickerOptions, type FieldKey, type PickerOption } from './bindingCards'
import type { InputRefValue } from './nodeOptions'
import { NodeName } from './NodeName'
import styles from './BindingCards.module.css'

function StateFlag({ state }: { state: ComfyNodeInfo['state'] }) {
  const { t } = useI18n()
  if (state === 'unused') return <span className={styles.flag}>{t.comfyui.cards.stateUnused}</span>
  if (state === 'display') return <span className={`${styles.flag} ${styles.flagDim}`}>{t.comfyui.cards.stateDisplay}</span>
  return null
}

/** 「ほかの入力」を出す数(多いときは絞り込みを促す)。 */
const INLINE_LIMIT = 20

interface InlineInputPickerProps {
  field: FieldKey
  nodes: readonly ComfyNodeInfo[]
  candidates: readonly ComfyBindingCandidate[]
  value: InputRefValue | null
  onSelect: (ref: InputRefValue | null) => void
  /** 「差し込まない」を出すか。 */
  allowNone?: boolean
  /** 見出し(「別の入力を選ぶ」など)。 */
  label: string
}

export function InlineInputPicker({ field, nodes, candidates, value, onSelect, allowNone = false, label }: InlineInputPickerProps) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const [query, setQuery] = useState('')
  const name = useId()
  // 一覧はページのスクロールで読む(入れ物の中のスクロールを作らない)ので、ほかの入力は先頭だけ出す。
  const options = pickerOptions(nodes, field, candidates, query.trim(), INLINE_LIMIT)
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const isSelected = (opt: PickerOption) => value?.node === opt.node && value?.input === opt.input

  function renderOption(opt: PickerOption) {
    const node = byId.get(opt.node)
    return (
      <li key={`${opt.node}\u0000${opt.input}`}>
        <label className={styles.option} aria-disabled={opt.wired ? 'true' : undefined}>
          <input
            type="radio"
            name={name}
            checked={isSelected(opt)}
            disabled={opt.wired}
            onChange={() => onSelect({ node: opt.node, input: opt.input })}
          />
          <span className={styles.optionBody}>
            <NodeName id={opt.node} node={node} input={opt.input} showValue />
            {opt.wired && <span className={`${styles.flag} ${styles.flagDim}`}>{c.wiredInput}</span>}
            <StateFlag state={opt.state} />
          </span>
        </label>
      </li>
    )
  }

  return (
    <div className={styles.picker}>
      <div className={styles.pickerHead}>
        <span className={styles.pickerLabel}>{label}</span>
        <input
          type="search"
          className={styles.pickerFilter}
          placeholder={c.pickerFilterPlaceholder}
          aria-label={c.pickerFilterPlaceholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className={styles.pickerList}>
        {allowNone && (
          <label className={styles.option}>
            <input type="radio" name={name} checked={value === null} onChange={() => onSelect(null)} />
            <span className={styles.optionBody}>{c.notBound}</span>
          </label>
        )}
        {options.candidates.length > 0 && (
          <>
            <p className={styles.optGroup}>{c.pickerCandidates}</p>
            <ul className={styles.optList}>{options.candidates.map(renderOption)}</ul>
          </>
        )}
        <p className={styles.optGroup}>{c.pickerOthers}</p>
        {options.others.length === 0 ? (
          <p className={styles.pickerEmpty}>{c.pickerNoMatch}</p>
        ) : (
          <ul className={styles.optList}>{options.others.map(renderOption)}</ul>
        )}
        {options.hiddenCount > 0 && (
          <p className={styles.pickerEmpty}>{fmt(c.pickerMore, { count: options.hiddenCount })}</p>
        )}
      </div>
    </div>
  )
}

interface InlineNodePickerProps {
  field: 'outputs' | 'final_prompt'
  nodes: readonly ComfyNodeInfo[]
  readingNodes: readonly string[]
  /** 複数選ぶ(出力)か、1つ(最終プロンプト)か。 */
  multiple: boolean
  value: readonly string[]
  onChange: (ids: string[]) => void
  label: string
}

export function InlineNodePicker({ field, nodes, readingNodes, multiple, value, onChange, label }: InlineNodePickerProps) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const [query, setQuery] = useState('')
  const name = useId()
  const { candidates, others: allOthers } = nodePickerOptions(nodes, field, readingNodes, query.trim())
  const others = allOthers.slice(0, INLINE_LIMIT)
  const hiddenCount = allOthers.length - others.length

  function toggle(id: string, checked: boolean) {
    if (!multiple) {
      onChange(checked ? [id] : [])
      return
    }
    onChange(checked ? (value.includes(id) ? [...value] : [...value, id]) : value.filter((v) => v !== id))
  }

  function renderNode(node: ComfyNodeInfo) {
    return (
      <li key={node.id}>
        <label className={styles.option}>
          <input
            type={multiple ? 'checkbox' : 'radio'}
            name={name}
            checked={value.includes(node.id)}
            onChange={(e) => toggle(node.id, e.target.checked)}
          />
          <span className={styles.optionBody}>
            <NodeName id={node.id} node={node} />
            <StateFlag state={node.state} />
          </span>
        </label>
      </li>
    )
  }

  return (
    <div className={styles.picker}>
      <div className={styles.pickerHead}>
        <span className={styles.pickerLabel}>{label}</span>
        <input
          type="search"
          className={styles.pickerFilter}
          placeholder={c.pickerFilterPlaceholder}
          aria-label={c.pickerFilterPlaceholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className={styles.pickerList}>
        {!multiple && (
          <label className={styles.option}>
            <input type="radio" name={name} checked={value.length === 0} onChange={() => onChange([])} />
            <span className={styles.optionBody}>{c.notBound}</span>
          </label>
        )}
        {candidates.length > 0 && (
          <>
            <p className={styles.optGroup}>{c.pickerCandidates}</p>
            <ul className={styles.optList}>{candidates.map(renderNode)}</ul>
          </>
        )}
        <p className={styles.optGroup}>{c.pickerOthers}</p>
        {others.length === 0 ? (
          <p className={styles.pickerEmpty}>{c.pickerNoMatch}</p>
        ) : (
          <ul className={styles.optList}>{others.map(renderNode)}</ul>
        )}
        {hiddenCount > 0 && <p className={styles.pickerEmpty}>{fmt(c.pickerMore, { count: hiddenCount })}</p>}
      </div>
    </div>
  )
}

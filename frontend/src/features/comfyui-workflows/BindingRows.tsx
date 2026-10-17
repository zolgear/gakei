/**
 * 差し込み先の行(ADR-0052 4章)。1項目1行で、値、理由の一文、確信度のバッジを出す。
 * 「道筋と変更」で開くと、道筋と行内のピッカーが出る。ranked と ask の行は最初から開いておく
 * (開くかどうかは親が持つ)。行の id は検証の一覧から移動するのに使う(`bindingRowId`)。
 */
import type { ReactNode } from 'react'
import type {
  ComfyBindingReading,
  ComfyNodeInfo,
  ComfyOperation,
  ComfySuggestedBindings,
  ComfyValidationIssue,
} from '../../api/client'
import { fmt, useI18n, type Messages } from '../../i18n'
import {
  bindingRowId,
  issuesForField,
  questionForField,
  REQUIRED_FIELDS,
  rowBadge,
  rowLevel,
  savedComparison,
  type FieldKey,
  type PendingQuestion,
  type RowBadge,
  type RowOrigins,
} from './bindingCards'
import { moveImageSlot, type BindingsFormState, type MaskMode } from './bindingsForm'
import { InlineInputPicker, InlineNodePicker } from './InlinePickers'
import type { InputRefValue } from './nodeOptions'
import { NodeName, NodePath } from './NodeName'
import styles from './BindingCards.module.css'

function fieldLabel(t: Messages, field: FieldKey): string {
  return t.comfyui.cards.fields[field]
}

export function ConfidenceBadge({ badge }: { badge: RowBadge }) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  return (
    <span className={styles.badge} data-badge={badge} title={c.badgeTips[badge]}>
      {c.badges[badge]}
    </span>
  )
}

interface BindingRowsProps {
  fields: readonly FieldKey[]
  operation: ComfyOperation
  readings: Partial<Record<FieldKey, ComfyBindingReading>>
  origins: RowOrigins
  bindings: BindingsFormState
  nodes: readonly ComfyNodeInfo[]
  /** 今の読み取りの値(編集画面で保存済みの値と比べる)。読み取りが無いときは null。 */
  suggested: ComfySuggestedBindings | null
  issues: readonly ComfyValidationIssue[]
  questions: readonly PendingQuestion[]
  expanded: ReadonlySet<FieldKey>
  onToggle: (field: FieldKey) => void
  /** 行の値を手で変える(出どころを「手で選択」にする)。 */
  onChange: (field: FieldKey, update: (prev: BindingsFormState) => BindingsFormState) => void
  /** 項目を読み取りの値に戻す(編集画面で、読み取りと違う行)。 */
  onUseReading: (field: FieldKey) => void
  /** 少しの間、強調する行(検証の一覧から移動したとき)。 */
  flashField: FieldKey | null
}

export function BindingRows(props: BindingRowsProps) {
  return (
    <ul className={styles.rows}>
      {props.fields.map((field) => (
        <BindingRow key={field} field={field} {...props} />
      ))}
    </ul>
  )
}

function BindingRow({
  field,
  operation,
  readings,
  origins,
  bindings,
  nodes,
  suggested,
  issues,
  questions,
  expanded,
  onToggle,
  onChange,
  onUseReading,
  flashField,
}: BindingRowsProps & { field: FieldKey }) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const reading = readings[field]
  const origin = origins[field]
  const badge = rowBadge(reading, origin)
  const rowIssues = issuesForField(issues, field)
  const level = rowLevel(rowIssues)
  const open = expanded.has(field)
  const question = questionForField(questions, field)
  const comparison = origin === 'saved' && suggested ? savedComparison(bindings, suggested, reading, field) : null

  let why: ReactNode
  if (question) why = c.whyQuestion
  else if (origin === 'manual') why = c.whyManual
  else if (origin === 'saved') why = c.whySaved
  else why = reading?.reason ?? c.whyNoReading

  const paths = reading?.paths ?? []
  return (
    <li
      id={bindingRowId(field)}
      className={styles.row}
      data-level={level ?? undefined}
      data-flash={flashField === field ? 'true' : undefined}
      tabIndex={-1}
    >
      <div className={styles.rowMain}>
        <div className={styles.rowLabel}>
          <span>{fieldLabel(t, field)}</span>
          {REQUIRED_FIELDS.has(field) && <span className={styles.required}>{c.required}</span>}
        </div>
        <div className={styles.rowValue}>
          <FieldValue field={field} bindings={bindings} nodes={nodes} />
          <p className={styles.why}>{why}</p>
          {origin !== 'reading' && reading && comparison === 'differ' && (
            <p className={styles.why}>{fmt(c.readingSays, { reason: reading.reason })}</p>
          )}
          {rowIssues.map((issue, i) => (
            <p key={i} className={styles.rowIssue} data-level={issue.level}>
              {fmt(issue.level === 'error' ? c.rowError : c.rowWarning, { message: issue.message })}
            </p>
          ))}
        </div>
        <div className={styles.rowSide}>
          <ConfidenceBadge badge={badge} />
          {comparison && (
            <span className={styles.compareChip} data-compare={comparison}>
              {comparison === 'match' ? c.readingMatch : comparison === 'differ' ? c.readingDiffer : c.readingUndecided}
            </span>
          )}
          <button
            type="button"
            className={styles.ghostButton}
            aria-expanded={open}
            aria-controls={`${bindingRowId(field)}-more`}
            onClick={() => onToggle(field)}
          >
            {open ? c.close : c.pathAndChange}
          </button>
        </div>
      </div>
      {open && (
        <div className={styles.rowMore} id={`${bindingRowId(field)}-more`}>
          {paths.length > 0 ? (
            <div className={styles.paths}>
              {paths.map((steps, i) => (
                <NodePath key={i} steps={steps} />
              ))}
            </div>
          ) : (
            <p className={styles.why}>{c.noPath}</p>
          )}
          {comparison === 'differ' && (
            <div>
              <button type="button" className={styles.smallButton} onClick={() => onUseReading(field)}>
                {c.useReading}
              </button>
            </div>
          )}
          <FieldEditor
            field={field}
            operation={operation}
            reading={reading}
            bindings={bindings}
            nodes={nodes}
            onChange={(update) => onChange(field, update)}
          />
        </div>
      )}
    </li>
  )
}

function NoValue() {
  const { t } = useI18n()
  return <span className={styles.noValue}>{t.comfyui.cards.notBound}</span>
}

function RefValue({ value, nodes }: { value: InputRefValue | null; nodes: readonly ComfyNodeInfo[] }) {
  if (!value || value.node === '') return <NoValue />
  return <NodeName id={value.node} node={nodes.find((n) => n.id === value.node)} input={value.input} showValue />
}

function FieldValue({
  field,
  bindings,
  nodes,
}: {
  field: FieldKey
  bindings: BindingsFormState
  nodes: readonly ComfyNodeInfo[]
}) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const find = (id: string) => nodes.find((n) => n.id === id)
  switch (field) {
    case 'prompt':
      return <RefValue value={bindings.prompt} nodes={nodes} />
    case 'negative_prompt':
      return <RefValue value={bindings.negativePrompt} nodes={nodes} />
    case 'width':
      return <RefValue value={bindings.width} nodes={nodes} />
    case 'height':
      return <RefValue value={bindings.height} nodes={nodes} />
    case 'batch_size':
      return <RefValue value={bindings.batchSize} nodes={nodes} />
    case 'seed': {
      const seeds = bindings.seeds.filter((ref) => ref.node !== '')
      if (seeds.length === 0) return <NoValue />
      return (
        <ul className={styles.valueList}>
          {seeds.map((ref) => (
            <li key={`${ref.node}.${ref.input}`}>
              <RefValue value={ref} nodes={nodes} />
            </li>
          ))}
        </ul>
      )
    }
    case 'images': {
      const images = bindings.images.filter((ref) => ref.node !== '')
      if (images.length === 0) return <NoValue />
      return (
        <ul className={styles.valueList}>
          {images.map((ref, i) => (
            <li key={`${ref.node}.${ref.input}`} className={styles.slotLine}>
              <span className={styles.slotNo}>{fmt(c.slotNo, { index: i + 1 })}</span>
              <RefValue value={ref} nodes={nodes} />
            </li>
          ))}
        </ul>
      )
    }
    case 'mask':
      if (bindings.maskMode === 'image_alpha') return <span>{c.maskImageAlphaValue}</span>
      if (bindings.maskMode === 'load_image_mask') return <RefValue value={bindings.maskRef} nodes={nodes} />
      return <NoValue />
    case 'outputs':
      if (bindings.outputs.length === 0) return <NoValue />
      return (
        <ul className={styles.valueList}>
          {bindings.outputs.map((id) => (
            <li key={id}>
              <NodeName id={id} node={find(id)} />
            </li>
          ))}
        </ul>
      )
    case 'final_prompt':
      return bindings.finalPrompt ? <NodeName id={bindings.finalPrompt} node={find(bindings.finalPrompt)} /> : <NoValue />
  }
}

function FieldEditor({
  field,
  operation,
  reading,
  bindings,
  nodes,
  onChange,
}: {
  field: FieldKey
  operation: ComfyOperation
  reading: ComfyBindingReading | undefined
  bindings: BindingsFormState
  nodes: readonly ComfyNodeInfo[]
  onChange: (update: (prev: BindingsFormState) => BindingsFormState) => void
}) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const candidates = reading?.candidates ?? []
  const single = (
    key: 'prompt' | 'negativePrompt' | 'width' | 'height' | 'batchSize',
    allowNone: boolean,
  ) => (
    <InlineInputPicker
      field={field}
      nodes={nodes}
      candidates={candidates}
      value={bindings[key]}
      allowNone={allowNone}
      label={c.chooseOther}
      onSelect={(ref) => onChange((b) => ({ ...b, [key]: ref }))}
    />
  )
  switch (field) {
    case 'prompt':
      return single('prompt', false)
    case 'negative_prompt':
      return single('negativePrompt', true)
    case 'width':
      return single('width', true)
    case 'height':
      return single('height', true)
    case 'batch_size':
      return single('batchSize', true)
    case 'seed':
      return (
        <RefListEditor
          field="seed"
          refs={bindings.seeds}
          nodes={nodes}
          candidates={candidates}
          ordered={false}
          addLabel={c.addSeed}
          onChange={(seeds) => onChange((b) => ({ ...b, seeds }))}
        />
      )
    case 'images':
      return (
        <RefListEditor
          field="images"
          refs={bindings.images}
          nodes={nodes}
          candidates={candidates}
          ordered
          addLabel={c.addImageSlot}
          onChange={(images) => onChange((b) => ({ ...b, images }))}
        />
      )
    case 'mask':
      return <MaskEditor bindings={bindings} nodes={nodes} operation={operation} reading={reading} onChange={onChange} />
    case 'outputs':
      return (
        <InlineNodePicker
          field="outputs"
          nodes={nodes}
          readingNodes={candidates.map((cand) => cand.node)}
          multiple
          value={bindings.outputs}
          label={c.chooseOutputs}
          onChange={(outputs) => onChange((b) => ({ ...b, outputs }))}
        />
      )
    case 'final_prompt':
      return (
        <InlineNodePicker
          field="final_prompt"
          nodes={nodes}
          readingNodes={candidates.map((cand) => cand.node)}
          multiple={false}
          value={bindings.finalPrompt ? [bindings.finalPrompt] : []}
          label={c.chooseOther}
          onChange={(ids) => onChange((b) => ({ ...b, finalPrompt: ids[0] ?? null }))}
        />
      )
  }
}

function RefListEditor({
  field,
  refs,
  nodes,
  candidates,
  ordered,
  addLabel,
  onChange,
}: {
  field: 'seed' | 'images'
  refs: InputRefValue[]
  nodes: readonly ComfyNodeInfo[]
  candidates: ComfyBindingReading['candidates']
  ordered: boolean
  addLabel: string
  onChange: (refs: InputRefValue[]) => void
}) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const set = refs.filter((ref) => ref.node !== '')
  return (
    <div className={styles.listEditor}>
      {set.length > 0 && (
        <ul className={styles.itemList}>
          {set.map((ref, i) => (
            <li key={`${ref.node}.${ref.input}`} className={styles.item}>
              {ordered && <span className={styles.slotNo}>{fmt(c.slotNo, { index: i + 1 })}</span>}
              <span className={styles.itemBody}>
                <NodeName id={ref.node} node={nodes.find((n) => n.id === ref.node)} input={ref.input} />
              </span>
              <span className={styles.itemActions}>
                {ordered && (
                  <>
                    <button
                      type="button"
                      className={styles.iconButton}
                      aria-label={t.comfyui.form.moveUp}
                      disabled={i === 0}
                      onClick={() => onChange(moveImageSlot(set, i, 'up'))}
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      className={styles.iconButton}
                      aria-label={t.comfyui.form.moveDown}
                      disabled={i === set.length - 1}
                      onClick={() => onChange(moveImageSlot(set, i, 'down'))}
                    >
                      ▼
                    </button>
                  </>
                )}
                <button type="button" className={styles.smallButton} onClick={() => onChange(set.filter((_, j) => j !== i))}>
                  {c.removeItem}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <InlineInputPicker
        field={field}
        nodes={nodes}
        candidates={candidates ?? []}
        value={null}
        label={addLabel}
        onSelect={(ref) => {
          if (!ref || set.some((r) => r.node === ref.node && r.input === ref.input)) return
          onChange([...set, ref])
        }}
      />
    </div>
  )
}

function MaskEditor({
  bindings,
  nodes,
  operation,
  reading,
  onChange,
}: {
  bindings: BindingsFormState
  nodes: readonly ComfyNodeInfo[]
  operation: ComfyOperation
  reading: ComfyBindingReading | undefined
  onChange: (update: (prev: BindingsFormState) => BindingsFormState) => void
}) {
  const { t } = useI18n()
  const c = t.comfyui.cards
  const f = t.comfyui.form
  const modes: { mode: MaskMode; label: string }[] = [
    { mode: 'none', label: f.maskModeNone },
    { mode: 'load_image_mask', label: f.maskModeLoadImageMask },
    { mode: 'image_alpha', label: f.maskModeImageAlpha },
  ]
  if (operation !== 'edit') return <p className={styles.why}>{c.maskGenerate}</p>
  return (
    <div className={styles.listEditor}>
      <div className={styles.radioRow} role="radiogroup" aria-label={c.fields.mask}>
        {modes.map(({ mode, label }) => (
          <label key={mode}>
            <input
              type="radio"
              name="binding-mask-mode"
              checked={bindings.maskMode === mode}
              onChange={() =>
                onChange((b) => ({ ...b, maskMode: mode, maskRef: mode === 'load_image_mask' ? b.maskRef : null }))
              }
            />
            {label}
          </label>
        ))}
      </div>
      {bindings.maskMode === 'image_alpha' && <p className={styles.why}>{c.maskImageAlphaHelp}</p>}
      {bindings.maskMode === 'load_image_mask' && (
        <InlineInputPicker
          field="mask"
          nodes={nodes}
          candidates={reading?.candidates ?? []}
          value={bindings.maskRef}
          label={c.chooseOther}
          onSelect={(ref) => onChange((b) => ({ ...b, maskRef: ref }))}
        />
      )}
    </div>
  )
}

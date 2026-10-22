/**
 * 図で選んだノードの入力の一覧(ADR-0052 4章)。入力ごとに「差し込む(項目を選ぶ)」「公開」「固定」を選ぶ。
 * 配線された入力は選べない(「配線」と出す)。使われない枝・表示だけのノードの入力には印を付ける。
 * 束を選んだときは中のノードの一覧を出し、選ぶとそのノードの入力の一覧になる。
 *
 * 割り当ての結果は第2段と同じ差し込み先と公開パラメーターの行に入り、同じ検証の API に掛かる。
 * テンプレートの値は変えない(値を書き換える欄は作らない)。
 *
 * 生成画面からの閲覧(ADR-0052 7章)では `readOnly` で使い、`valueNote` で今のフォームの値を添える。
 * 秘密に見える値として伏せた入力(閲覧の API の `redacted`)は、値の代わりに伏せたことを示す。
 */
import type { ReactNode } from 'react'
import type { ComfyNodeInfo, ComfyNodeInputInfo, ComfyOperation } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import {
  canExpose,
  choiceOf,
  choiceValue,
  fieldsForInput,
  inputFitsField,
  inputUse,
  isNodeField,
  nodeFitsBoundingBoxes,
  nodeFitsFinalPrompt,
  nodeFitsOutput,
  nodeRoles,
  parseChoiceValue,
  type InputChoice,
  type InputField,
  type InputUse,
  type MapEdge,
  type MapGraph,
} from './bindingGraph'
import { nodeDisplayTitle, shortNodeId, valuePreview, type FieldKey } from './bindingCards'
import type { BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'
import { groupTitle } from './bindingMapView'
import { NodeName } from './NodeName'
import styles from './BindingMap.module.css'

interface BindingMapInspectorProps {
  selected: string | null
  graph: MapGraph
  nodes: readonly ComfyNodeInfo[]
  edges: readonly MapEdge[]
  bindings: BindingsFormState
  exposedRows: readonly ExposedParamRow[]
  operation: ComfyOperation
  pickField: FieldKey | null
  readOnly: boolean
  onSelect: (id: string | null) => void
  onChooseInput: (nodeId: string, input: string, choice: InputChoice) => void
  onToggleOutput: (nodeId: string) => void
  onToggleFinalPrompt: (nodeId: string) => void
  onToggleBoundingBoxes: (nodeId: string) => void
  /** 入力の値の下に添える一行(閲覧で、差し込む入力・公開パラメーターの今のフォームの値)。 */
  valueNote?: (node: ComfyNodeInfo, input: ComfyNodeInputInfo, use: InputUse) => ReactNode
}

/** 閲覧の API が値を伏せた入力か(登録画面の analyze の入力には印が無い)。 */
function isRedacted(input: ComfyNodeInputInfo): boolean {
  return (input as ComfyNodeInputInfo & { redacted?: boolean }).redacted === true
}

export function BindingMapInspector(props: BindingMapInspectorProps) {
  const { t } = useI18n()
  const m = t.comfyui.map
  const { selected, graph, nodes } = props
  if (selected === null) {
    return (
      <div className={styles.inspector}>
        <p className={styles.inspectorEmpty}>{m.inspectorEmpty}</p>
      </div>
    )
  }
  const box = graph.boxes.find((b) => b.id === selected)
  if (box && box.nodeIds.length > 1) return <GroupMembers {...props} memberIds={box.nodeIds} />
  const node = nodes.find((n) => n.id === selected)
  if (!node) return null
  return <NodeInputs {...props} node={node} />
}

function GroupMembers({
  memberIds,
  nodes,
  bindings,
  exposedRows,
  pickField,
  onSelect,
}: BindingMapInspectorProps & { memberIds: string[] }) {
  const { t } = useI18n()
  const m = t.comfyui.map
  const roles = nodeRoles(bindings, exposedRows)
  const members = memberIds.map((id) => nodes.find((n) => n.id === id)).filter((n): n is ComfyNodeInfo => !!n)
  const state = members.reduce<NonNullable<ComfyNodeInfo['state']>>(
    (acc, n) => (acc === 'live' || n.state === 'live' ? 'live' : acc === 'display' || n.state === 'display' ? 'display' : 'unused'),
    'unused',
  )
  return (
    <div className={styles.inspector}>
      <div className={styles.inspectorHead}>
        <h3 className={styles.inspectorTitle}>
          {fmt(m.groupHeading, { title: groupTitle(members, state), count: members.length })}
        </h3>
        <button type="button" className={styles.smallButton} onClick={() => onSelect(null)}>
          {m.closeInspector}
        </button>
      </div>
      <p className={styles.inspectorLead}>{m.groupLead}</p>
      <ul className={styles.memberList}>
        {members.map((n) => {
          const pickable =
            pickField !== null &&
            (pickField === 'outputs'
              ? nodeFitsOutput(n)
              : pickField === 'final_prompt'
                ? nodeFitsFinalPrompt(n)
                : pickField === 'bounding_boxes'
                  ? nodeFitsBoundingBoxes(n)
                  : (n.inputs ?? []).some((i) => inputFitsField(n, i, pickField)))
          const roleCount = roles.get(n.id)?.length ?? 0
          return (
            <li key={n.id}>
              <button
                type="button"
                className={styles.memberButton}
                data-pickable={pickable || undefined}
                data-state={n.state}
                onClick={() => onSelect(n.id)}
              >
                <NodeName id={n.id} node={n} />
                <span className={styles.memberMeta}>
                  {t.comfyui.cards.states[n.state ?? 'live']}
                  {roleCount > 0 ? ` ・ ${roleCount}` : ''}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function NodeInputs({
  node,
  graph,
  nodes,
  edges,
  bindings,
  exposedRows,
  operation,
  pickField,
  readOnly,
  onSelect,
  onChooseInput,
  onToggleOutput,
  onToggleFinalPrompt,
  onToggleBoundingBoxes,
  valueNote,
}: BindingMapInspectorProps & { node: ComfyNodeInfo }) {
  const { t } = useI18n()
  const m = t.comfyui.map
  const c = t.comfyui.cards
  const roles = nodeRoles(bindings, exposedRows)
  const state = node.state ?? 'live'
  const group = graph.boxes.find((b) => b.nodeIds.length > 1 && b.nodeIds.includes(node.id))
  const inputs = node.inputs ?? []
  const isOutput = bindings.outputs.includes(node.id)
  const isFinal = bindings.finalPrompt === node.id
  const canOutput = nodeFitsOutput(node) || isOutput
  const canFinal = nodeFitsFinalPrompt(node) || isFinal
  const isBoxes = bindings.boundingBoxes === node.id
  const canBoxes = nodeFitsBoundingBoxes(node) || isBoxes

  return (
    <div className={styles.inspector}>
      <div className={styles.inspectorHead}>
        <h3 className={styles.inspectorTitle}>
          <NodeName id={node.id} node={node} />
        </h3>
        <button type="button" className={styles.smallButton} onClick={() => onSelect(null)}>
          {m.closeInspector}
        </button>
      </div>
      <p className={styles.stateLine} data-state={state}>
        <span className={styles.stateDot} aria-hidden="true" />
        {c.states[state]} — {m.stateHelp[state]}
      </p>
      {group && (
        <button type="button" className={styles.linkButton} onClick={() => onSelect(group.id)}>
          {m.backToGroup}
        </button>
      )}

      {!readOnly && (canOutput || canFinal || canBoxes) && (
        <div className={styles.nodeActions}>
          {canOutput && (
            <button
              type="button"
              className={styles.smallButton}
              data-primary={pickField === 'outputs' && !isOutput ? 'true' : undefined}
              onClick={() => onToggleOutput(node.id)}
            >
              {isOutput ? m.removeOutput : m.makeOutput}
            </button>
          )}
          {canFinal && (
            <button
              type="button"
              className={styles.smallButton}
              data-primary={pickField === 'final_prompt' && !isFinal ? 'true' : undefined}
              onClick={() => onToggleFinalPrompt(node.id)}
            >
              {isFinal ? m.removeFinalPrompt : m.makeFinalPrompt}
            </button>
          )}
          {canBoxes && (
            <button
              type="button"
              className={styles.smallButton}
              data-primary={pickField === 'bounding_boxes' && !isBoxes ? 'true' : undefined}
              onClick={() => onToggleBoundingBoxes(node.id)}
            >
              {isBoxes ? m.removeBoundingBoxes : m.makeBoundingBoxes}
            </button>
          )}
        </div>
      )}

      <h4 className={styles.inputsHeading}>{m.inputsHeading}</h4>
      {inputs.length === 0 ? (
        <p className={styles.inspectorLead}>{m.noInputs}</p>
      ) : (
        <ul className={styles.inputList}>
          {inputs.map((input) => {
            const use = inputUse(node, input, roles)
            return (
              <InputRow
                key={input.name}
                node={node}
                input={input}
                state={state}
                use={use}
                note={valueNote?.(node, input, use)}
                nodes={nodes}
                edges={edges}
                operation={operation}
                pickField={pickField}
                readOnly={readOnly}
                onChoose={(choice) => onChooseInput(node.id, input.name, choice)}
              />
            )
          })}
        </ul>
      )}
    </div>
  )
}

function InputRow({
  node,
  input,
  state,
  use,
  nodes,
  edges,
  operation,
  pickField,
  readOnly,
  onChoose,
  note,
}: {
  node: ComfyNodeInfo
  input: ComfyNodeInputInfo
  state: NonNullable<ComfyNodeInfo['state']>
  use: ReturnType<typeof inputUse>
  note?: ReactNode
  nodes: readonly ComfyNodeInfo[]
  edges: readonly MapEdge[]
  operation: ComfyOperation
  pickField: FieldKey | null
  readOnly: boolean
  onChoose: (choice: InputChoice) => void
}) {
  const { t } = useI18n()
  const m = t.comfyui.map
  const c = t.comfyui.cards
  const fields = fieldsForInput(node, input, operation)
  const choice = choiceOf(use)
  const pickHere =
    !readOnly && pickField !== null && !isNodeField(pickField) && fields.includes(pickField)
  const id = `map-input-${node.id}-${input.name}`.replace(/[^a-zA-Z0-9_-]/g, '_')

  let valueText: string
  if (input.linked) {
    const edge = edges.find((e) => e.target === node.id && e.input === input.name)
    const source = edge ? nodes.find((n) => n.id === edge.source) : undefined
    valueText = fmt(m.wiredFrom, {
      source: edge ? `${shortNodeId(edge.source)} ${nodeDisplayTitle(source) ?? source?.class_type ?? ''}`.trim() : '?',
    })
  } else if (isRedacted(input)) {
    valueText = m.redactedValue
  } else if (typeof input.value === 'string') {
    const preview = valuePreview(input.value, 80)
    valueText = preview === '' ? c.emptyValue : `「${preview}」`
  } else {
    valueText = input.value === null || input.value === undefined ? '—' : String(input.value)
  }

  const useLabel =
    use.kind === 'wired'
      ? m.useLabels.wired
      : use.kind === 'exposed'
        ? m.useLabels.exposed
        : use.kind === 'fixed'
          ? m.useLabels.fixed
          : use.fields.map((f) => fmt(m.useBind, { field: c.fields[f] })).join(' / ')

  return (
    <li className={styles.inputRow} data-use={use.kind} data-pick={pickHere || undefined}>
      <div className={styles.inputHead}>
        <label className={styles.inputName} htmlFor={readOnly || input.linked ? undefined : id}>
          {input.name}
        </label>
        {!input.linked && state !== 'live' && (
          <span className={styles.inputMark} data-state={state}>
            {state === 'unused' ? m.inputUnused : m.inputDisplay}
          </span>
        )}
      </div>
      <div className={styles.inputValue} data-wired={input.linked || undefined} data-redacted={isRedacted(input) || undefined}>
        {valueText}
        {note ? <span className={styles.inputNote}>{note}</span> : null}
      </div>
      {input.linked ? (
        <span className={styles.useChip} data-use="wired" title={m.wiredHelp}>
          {m.useLabels.wired}
        </span>
      ) : readOnly || choice === null ? (
        <span className={styles.useChip} data-use={use.kind}>
          {useLabel}
        </span>
      ) : (
        <select
          id={id}
          className={styles.useSelect}
          data-use={use.kind}
          aria-label={fmt(m.inputRoleLabel, { input: input.name })}
          value={choiceValue(choice)}
          onChange={(e) => {
            const next = parseChoiceValue(e.target.value)
            if (next) onChoose(next)
          }}
        >
          <option value="fixed">{m.useFixed}</option>
          {(canExpose(input) || use.kind === 'exposed') && <option value="exposed">{m.useExposed}</option>}
          {fields.length > 0 && (
            <optgroup label={m.useBindGroup}>
              {fields.map((f) => (
                <option key={f} value={`bind:${f}`}>
                  {fmt(m.useBind, { field: c.fields[f] })}
                </option>
              ))}
            </optgroup>
          )}
          {use.kind === 'bound' &&
            use.fields
              .filter((f) => !(fields as readonly string[]).includes(f))
              .map((f) => (
                <option key={f} value={`bind:${f}`}>
                  {fmt(m.useBind, { field: c.fields[f] })}
                </option>
              ))}
        </select>
      )}
      {pickHere && pickField !== null && (
        <button
          type="button"
          className={styles.smallButton}
          data-primary="true"
          onClick={() => onChoose({ kind: 'bind', field: pickField as InputField })}
        >
          {fmt(m.assignHere, { field: c.fields[pickField] })}
        </button>
      )}
    </li>
  )
}

/**
 * 生成画面のメインエリアの「ワークフロー」(ADR-0052 7章)。選んでいる ComfyUI のワークフローを読むだけの図で見せる。
 *
 * - 図は第3段の `BindingMap`(`variant="viewer"`、読むだけ)をそのまま使う。縮約 / すべてのノード、状態の描き分け。
 * - パネル: ワークフローの名前(管理者には登録画面へのリンク)、選んだノードの入力ごとの扱いと値
 *   (`BindingMapInspector` を読むだけで使う)、差し込み先と公開パラメーターの一覧。
 * - 差し込む入力と公開パラメーターには今のフォームの値を添える(未入力ならテンプレートの値。`workflowViewValues.ts`)。
 * - 割り当ての変更・保存はしない。値は閲覧用の API(`GET /api/comfyui/workflows/{id}/view`)から読み、
 *   秘密に見える値は伏せてある。
 * - スクロール: この部品の外枠(`.scroll`)がメインエリアのスクロールで、ほかに縦のスクロールは作らない。
 *   図はその上に留まり、ホイールは外枠に渡す(`BindingMap`)。768px 未満は外枠もスクロールせず、ページと一緒に流れる。
 */
import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import {
  ApiError,
  viewComfyWorkflow,
  type ComfyNodeInfo,
  type ComfyNodeInputInfo,
  type ComfyViewNode,
} from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { isAdmin, useAuth } from '../auth/authState'
import { BindingMap } from './BindingMap'
import { BindingMapInspector } from './BindingMapInspector'
import { isNodeField, mapEdges, type InputField, type InputUse } from './bindingGraph'
import { valuePreview } from './bindingCards'
import { bindingsFormFromApi } from './bindingsForm'
import { NodeName } from './NodeName'
import {
  boundInputValue,
  exposedParamValue,
  findViewInput,
  viewEntries,
  viewExposedRows,
  type ShownValue,
  type ViewEntry,
  type ViewFormValues,
} from './workflowViewValues'
import styles from './WorkflowViewer.module.css'

const NO_READINGS: [] = []
const noop = () => {}

interface WorkflowViewerProps {
  workflowId: string
  form: ViewFormValues
}

export function WorkflowViewer({ workflowId, form }: WorkflowViewerProps) {
  const { t } = useI18n()
  const v = t.comfyui.viewer
  const admin = isAdmin(useAuth())
  const [selected, setSelected] = useState<string | null>(null)
  const inspectorRef = useRef<HTMLDivElement | null>(null)
  const query = useQuery({
    queryKey: ['comfyui-workflow-view', workflowId],
    queryFn: () => viewComfyWorkflow(workflowId),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  })
  const view = query.data

  const nodes = useMemo<ComfyViewNode[]>(() => view?.nodes ?? [], [view])
  const edges = useMemo(() => mapEdges(nodes, view?.edges), [nodes, view])
  const bindings = useMemo(() => (view ? bindingsFormFromApi(view.bindings) : null), [view])
  const exposedRows = useMemo(() => viewExposedRows(view?.exposed_params ?? []), [view])
  const entries = useMemo(
    () => (bindings ? viewEntries(bindings, view?.exposed_params ?? []) : []),
    [bindings, view],
  )

  if (query.isPending) {
    return <p className={styles.status}>{v.loading}</p>
  }
  if (query.isError || !view || !bindings) {
    const notFound = query.error instanceof ApiError && query.error.status === 404
    return <p className={styles.status}>{notFound ? v.notFound : v.loadFailed}</p>
  }

  function formatShown(shown: ShownValue): string {
    const text = (value: unknown) => {
      if (typeof value === 'string') {
        const preview = valuePreview(value, 80)
        return preview === '' || preview === null ? v.valueEmpty : `「${preview}」`
      }
      return value === null || value === undefined ? v.valueEmpty : String(value)
    }
    switch (shown.source) {
      case 'form':
        return fmt(v.valueForm, { value: text(shown.value) })
      case 'default':
        return fmt(v.valueDefault, { value: text(shown.value) })
      case 'template':
        return fmt(v.valueTemplate, { value: text(shown.value) })
      case 'random':
        return v.valueRandom
      case 'redacted':
        return v.valueRedacted
      case 'input':
        return shown.slot === undefined ? v.valueMask : fmt(v.valueImage, { slot: shown.slot })
    }
  }

  // 図の入力の行に添える値(テンプレートの値と伏せた値は行にすでに出ているので、それ以外だけ)。
  const valueNote = (node: ComfyNodeInfo, input: ComfyNodeInputInfo, use: InputUse) => {
    const viewInput = findViewInput(nodes, node.id, input.name)
    let shown: ShownValue[] = []
    if (use.kind === 'bound') {
      const slotIndex = bindings.images.findIndex((r) => r.node === node.id && r.input === input.name)
      shown = use.fields
        .filter((f): f is InputField => !isNodeField(f))
        .map((f) => boundInputValue(f, form, viewInput, slotIndex >= 0 ? slotIndex + 1 : undefined))
    } else if (use.kind === 'exposed') {
      const param = view.exposed_params?.find((p) => p.node === node.id && p.input === input.name)
      if (param) shown = [exposedParamValue(param, form, viewInput)]
    }
    const lines = shown.filter((s) => s.source !== 'template' && s.source !== 'redacted').map(formatShown)
    return lines.length > 0 ? lines.join(' / ') : null
  }

  function entryValue(entry: ViewEntry): string | null {
    if (entry.kind === 'node') return null
    if (entry.kind === 'exposed') {
      return formatShown(exposedParamValue(entry.param, form, findViewInput(nodes, entry.param.node, entry.param.input)))
    }
    return formatShown(boundInputValue(entry.field, form, findViewInput(nodes, entry.node, entry.input), entry.slot))
  }

  const nodeById = new Map(nodes.map((n) => [n.id, n]))

  // 一覧から選んだときは、入力の一覧(パネルの上の方)が見えるところまで外側のスクロールを動かす。
  function selectFromEntry(nodeId: string) {
    setSelected(nodeId)
    requestAnimationFrame(() => inspectorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }

  return (
    <div className={styles.scroll}>
      <BindingMap
        variant="viewer"
        nodes={nodes}
        edges={edges}
        bindings={bindings}
        exposedRows={exposedRows}
        readings={NO_READINGS}
        focusField={null}
        pickField={null}
        onCancelPick={noop}
        selected={selected}
        onSelect={setSelected}
        readOnly
        onChooseInput={noop}
        onToggleOutput={noop}
        onToggleFinalPrompt={noop}
        onToggleBoundingBoxes={noop}
        renderPanel={(graph) => (
          <>
            <div className={styles.head}>
              <h2 className={styles.heading}>{fmt(v.heading, { name: view.name })}</h2>
              {admin && (
                <Link to={`/settings/comfyui/workflows/${view.id}`} className={styles.editLink}>
                  {v.editLink}
                </Link>
              )}
              <p className={styles.lead}>{v.lead}</p>
            </div>
            <div ref={inspectorRef} className={styles.inspectorAnchor}>
              <BindingMapInspector
                selected={selected}
                graph={graph}
                nodes={nodes}
                edges={edges}
                bindings={bindings}
                exposedRows={exposedRows}
                operation={view.operation}
                pickField={null}
                readOnly
                onSelect={setSelected}
                onChooseInput={noop}
                onToggleOutput={noop}
                onToggleFinalPrompt={noop}
                onToggleBoundingBoxes={noop}
                valueNote={valueNote}
              />
            </div>
            <section className={styles.entries} aria-labelledby="workflow-view-entries">
              <h3 id="workflow-view-entries" className={styles.entriesHeading}>
                {v.entriesHeading}
              </h3>
              <ul className={styles.entryList}>
                {entries.map((entry, i) => {
                  const nodeId = entry.kind === 'exposed' ? entry.param.node : entry.node
                  const label =
                    entry.kind === 'exposed'
                      ? fmt(v.exposedLabel, { label: entry.param.label })
                      : entry.kind === 'bound' && entry.field === 'images' && entry.slot !== undefined
                        ? `${t.comfyui.cards.fields.images} ${fmt(t.comfyui.map.badgeSlot, { index: entry.slot })}`
                        : t.comfyui.cards.fields[entry.field]
                  const value = entryValue(entry)
                  return (
                    <li key={`${entry.kind}-${i}`}>
                      <button
                        type="button"
                        className={styles.entryButton}
                        data-kind={entry.kind}
                        aria-pressed={selected === nodeId}
                        onClick={() => selectFromEntry(nodeId)}
                      >
                        <span className={styles.entryLabel}>{label}</span>
                        <span className={styles.entryNode}>
                          <NodeName
                            id={nodeId}
                            node={nodeById.get(nodeId)}
                            input={entry.kind === 'exposed' ? entry.param.input : entry.kind === 'bound' ? entry.input : null}
                          />
                        </span>
                        {value && <span className={styles.entryValue}>{value}</span>}
                      </button>
                    </li>
                  )
                })}
              </ul>
            </section>
          </>
        )}
      />
    </div>
  )
}

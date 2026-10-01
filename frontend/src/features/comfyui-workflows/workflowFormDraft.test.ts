import { describe, expect, it } from 'vitest'
import type { ComfyExposedParam, ComfyNodeInfo } from '../../api/client'
import { EMPTY_BINDINGS_FORM } from './bindingsForm'
import {
  EMPTY_WORKFLOW_FORM,
  applyAnalyzedTemplate,
  changedWorkflowFormKeys,
  isOpenedFromComfyUIPage,
  FROM_COMFYUI_PAGE_STATE,
  withOperation,
  workflowFormBackDestination,
  workflowFormFromSaved,
  workflowFormPath,
  type WorkflowFormValues,
} from './workflowFormDraft'

const NODES: ComfyNodeInfo[] = [
  { id: '1', class_type: 'CLIPTextEncode', title: null, inputs: [{ name: 'text', value: '', linked: false }] },
  { id: '2', class_type: 'KSampler', title: null, inputs: [{ name: 'seed', value: 0, linked: false }] },
  { id: '3', class_type: 'SaveImage', title: null, inputs: [] },
]

const STEPS: ComfyExposedParam = { name: 'steps', node: '2', input: 'steps', type: 'int', label: 'steps', default: 20 }

const TEMPLATE = { '1': { class_type: 'CLIPTextEncode', inputs: { text: '' } } }

function form(overrides: Partial<WorkflowFormValues> = {}): WorkflowFormValues {
  return { ...EMPTY_WORKFLOW_FORM, ...overrides }
}

describe('changedWorkflowFormKeys', () => {
  it('同じ値なら変更なし', () => {
    expect(changedWorkflowFormKeys(EMPTY_WORKFLOW_FORM, form())).toEqual([])
  })

  it('変えた欄を画面の順に返す', () => {
    const values = form({ name: 'a', template: TEMPLATE, operation: 'edit' })
    expect(changedWorkflowFormKeys(EMPTY_WORKFLOW_FORM, values)).toEqual(['name', 'operation', 'template'])
  })

  it('ノードの選択肢は数えない', () => {
    expect(changedWorkflowFormKeys(EMPTY_WORKFLOW_FORM, form({ nodes: NODES }))).toEqual([])
  })

  it('中身が同じなら別のオブジェクトでも変更なし(元に戻したとき)', () => {
    const saved = form({ bindings: { ...EMPTY_BINDINGS_FORM, outputs: ['3'] } })
    const values = form({ bindings: { ...EMPTY_BINDINGS_FORM, outputs: ['3'] } })
    expect(changedWorkflowFormKeys(saved, values)).toEqual([])
  })

  it('差し込み先と公開パラメーターの中の変化も拾う', () => {
    const saved = form({ bindings: { ...EMPTY_BINDINGS_FORM, outputs: ['3'] } })
    const values = form({ bindings: { ...EMPTY_BINDINGS_FORM, outputs: [] }, exposedRows: [] })
    expect(changedWorkflowFormKeys(saved, values)).toEqual(['bindings'])
  })
})

describe('workflowFormFromSaved', () => {
  const wf = {
    name: 'flux',
    operation: 'generate' as const,
    template: TEMPLATE,
    bindings: { prompt: { node: '1', input: 'text' }, seed: [], outputs: ['3'] },
    exposed_params: [STEPS],
  }

  it('保存済みの bindings / exposed_params を初期値にし、ノードは analyze から取る', () => {
    const values = workflowFormFromSaved(wf, { nodes: NODES, candidate_params: [] })
    expect(values.name).toBe('flux')
    expect(values.template).toBe(TEMPLATE)
    expect(values.nodes).toBe(NODES)
    expect(values.bindings.prompt).toEqual({ node: '1', input: 'text' })
    expect(values.exposedRows.map((r) => [r.name, r.enabled])).toEqual([['steps', true]])
  })

  it('analyze に失敗しても保存済みの値は残す', () => {
    const values = workflowFormFromSaved(wf, null)
    expect(values.nodes).toEqual([])
    expect(values.bindings.outputs).toEqual(['3'])
    expect(values.exposedRows.map((r) => r.name)).toEqual(['steps'])
  })
})

describe('applyAnalyzedTemplate', () => {
  const result = {
    nodes: NODES,
    candidate_params: [STEPS],
    suggested_bindings: { prompt: { node: '1', input: 'text' }, outputs: ['3'] },
    suggested_operation: 'edit' as const,
  }

  it('初めてのファイルは提案を初期値にする(名前はそのまま)', () => {
    const { values, cleared } = applyAnalyzedTemplate(form({ name: 'mine' }), TEMPLATE, result)
    expect(values.name).toBe('mine')
    expect(values.template).toBe(TEMPLATE)
    expect(values.operation).toBe('edit')
    expect(values.nodes).toBe(NODES)
    expect(values.bindings.prompt).toEqual({ node: '1', input: 'text' })
    expect(values.exposedRows.map((r) => [r.name, r.enabled])).toEqual([['steps', false]])
    expect(cleared).toEqual([])
  })

  it('差し替えでは今の値を保ち、新しいファイルに無い差し込み先だけ未設定に戻す', () => {
    const prev = form({
      operation: 'generate',
      template: { old: {} },
      bindings: { ...EMPTY_BINDINGS_FORM, prompt: { node: '9', input: 'text' }, outputs: ['3'] },
    })
    const { values, cleared } = applyAnalyzedTemplate(prev, TEMPLATE, result)
    expect(values.operation).toBe('generate')
    expect(values.template).toBe(TEMPLATE)
    expect(values.bindings.prompt).toBeNull()
    expect(values.bindings.outputs).toEqual(['3'])
    expect(cleared.length).toBe(1)
  })
})

describe('withOperation', () => {
  it('generate に切り替えると入力画像とマスクを落とす', () => {
    const prev = form({
      operation: 'edit',
      bindings: {
        ...EMPTY_BINDINGS_FORM,
        images: [{ node: '4', input: 'image' }],
        maskMode: 'load_image_mask',
        maskRef: { node: '5', input: 'image' },
      },
    })
    const next = withOperation(prev, 'generate')
    expect(next.operation).toBe('generate')
    expect(next.bindings.images).toEqual([])
    expect(next.bindings.maskMode).toBe('none')
    expect(next.bindings.maskRef).toBeNull()
  })

  it('edit に切り替えても差し込み先はそのまま', () => {
    const prev = form({ bindings: { ...EMPTY_BINDINGS_FORM, outputs: ['3'] } })
    expect(withOperation(prev, 'edit').bindings).toBe(prev.bindings)
  })
})

describe('workflowFormPath', () => {
  it('登録と編集のパス', () => {
    expect(workflowFormPath()).toBe('/settings/comfyui/workflows/new')
    expect(workflowFormPath('abc')).toBe('/settings/comfyui/workflows/abc')
  })
})

describe('workflowFormBackDestination', () => {
  it('ComfyUI のページから開いたなら1つ戻る', () => {
    expect(workflowFormBackDestination({ openedFromComfyUIPage: true, locationKey: 'k1' })).toEqual({ type: 'back' })
  })

  it('直接開いた・ほかの画面から来たときは ComfyUI のページへ置き換える', () => {
    const replace = { type: 'replace', path: '/settings/comfyui' }
    expect(workflowFormBackDestination({ openedFromComfyUIPage: true, locationKey: 'default' })).toEqual(replace)
    expect(workflowFormBackDestination({ openedFromComfyUIPage: false, locationKey: 'k1' })).toEqual(replace)
  })
})

describe('isOpenedFromComfyUIPage', () => {
  it('ComfyUI のページのリンクが付けた印だけを見る', () => {
    expect(isOpenedFromComfyUIPage(FROM_COMFYUI_PAGE_STATE)).toBe(true)
    expect(isOpenedFromComfyUIPage(null)).toBe(false)
    expect(isOpenedFromComfyUIPage({ fromSettingsToc: true })).toBe(false)
  })
})

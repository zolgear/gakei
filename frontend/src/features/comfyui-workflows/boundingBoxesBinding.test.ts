/**
 * 四角の差し込み先(ADR-0053 6章)。Create Bounding Boxes のノードだけを選べ、`bboxes` に差し込む。
 */
import { describe, expect, it } from 'vitest'
import type { ComfyNodeInfo, ComfySuggestedBindings } from '../../api/client'
import { fieldValueKey, nodePickerOptions, visibleFields, withReadingValue } from './bindingCards'
import { isNodeField, nodePickable, nodeRoles, toggleBoundingBoxes } from './bindingGraph'
import {
  EMPTY_BINDINGS_FORM,
  bindingsFormFromApi,
  bindingsFormFromSuggestion,
  bindingsFormToApi,
  reconcileBindingsWithNodes,
} from './bindingsForm'
import { viewEntries } from './workflowViewValues'

const nodes: ComfyNodeInfo[] = [
  { id: '6', class_type: 'CLIPTextEncode', state: 'live', title: null, inputs: [{ name: 'text', value: 'x', linked: false }] },
  { id: '9', class_type: 'SaveImage', state: 'live', title: null, inputs: [] },
  {
    id: '20',
    class_type: 'CreateBoundingBoxes',
    state: 'live',
    title: null,
    inputs: [{ name: 'width', value: 1024, linked: false }],
  },
]

describe('四角の差し込み先', () => {
  it('API の形と行き来する(入力はいつも bboxes)', () => {
    const form = bindingsFormFromApi({
      prompt: { node: '6', input: 'text' },
      outputs: ['9'],
      bounding_boxes: { node: '20', input: 'bboxes' },
    })
    expect(form.boundingBoxes).toBe('20')
    expect(bindingsFormToApi(form).bounding_boxes).toEqual({ node: '20', input: 'bboxes' })
    expect(bindingsFormToApi({ ...form, boundingBoxes: null }).bounding_boxes).toBeNull()
    // 旧形式(キー無し)は「なし」。
    expect(bindingsFormFromApi({ prompt: { node: '6', input: 'text' }, outputs: ['9'] }).boundingBoxes).toBeNull()
  })

  it('読み取りの値を使える', () => {
    const suggested: ComfySuggestedBindings = { outputs: ['9'], bounding_boxes: { node: '20', input: 'bboxes' } }
    expect(bindingsFormFromSuggestion(suggested).boundingBoxes).toBe('20')
    const form = withReadingValue({ ...EMPTY_BINDINGS_FORM }, suggested, 'bounding_boxes')
    expect(fieldValueKey(form, 'bounding_boxes')).toBe('20')
  })

  it('テンプレートに Create Bounding Boxes があるときだけ行を出す', () => {
    expect(visibleFields('generate', nodes)).toContain('bounding_boxes')
    expect(visibleFields('generate', nodes.filter((n) => n.id !== '20'))).not.toContain('bounding_boxes')
  })

  it('選べるのは Create Bounding Boxes だけ', () => {
    const options = nodePickerOptions(nodes, 'bounding_boxes', [], '')
    expect(options.candidates.map((n) => n.id)).toEqual(['20'])
    expect(options.others).toEqual([])
    expect(nodePickable(nodes[2], 'bounding_boxes')).toBe(true)
    expect(nodePickable(nodes[0], 'bounding_boxes')).toBe(false)
    expect(isNodeField('bounding_boxes')).toBe(true)
  })

  it('図で付け外しでき、役割の札が付く', () => {
    const on = toggleBoundingBoxes({ ...EMPTY_BINDINGS_FORM }, '20')
    expect(on.boundingBoxes).toBe('20')
    expect(nodeRoles(on, []).get('20')).toEqual([{ role: 'bounding_boxes', input: null }])
    expect(toggleBoundingBoxes(on, '20').boundingBoxes).toBeNull()
    expect(viewEntries(on, [])).toContainEqual({ kind: 'node', field: 'bounding_boxes', node: '20' })
  })

  it('テンプレートを差し替えてノードが無くなれば外す', () => {
    const form = { ...EMPTY_BINDINGS_FORM, boundingBoxes: '20' }
    const kept = reconcileBindingsWithNodes(form, nodes)
    expect(kept.next.boundingBoxes).toBe('20')
    const cleared = reconcileBindingsWithNodes(form, nodes.slice(0, 2))
    expect(cleared.next.boundingBoxes).toBeNull()
    expect(cleared.clearedFields).toHaveLength(1)
  })
})

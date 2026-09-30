import { describe, expect, it } from 'vitest'
import { buildWorkflowCreateRequest, buildWorkflowUpdateRequest } from './workflowSave'
import { EMPTY_BINDINGS_FORM } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'

describe('buildWorkflowCreateRequest / buildWorkflowUpdateRequest', () => {
  const template = { '1': { class_type: 'CLIPTextEncode', inputs: { text: '' } } }
  const bindings = { ...EMPTY_BINDINGS_FORM, prompt: { node: '1', input: 'text' }, outputs: ['3'] }
  const enabledRow: ExposedParamRow = {
    enabled: true,
    name: 'steps',
    node: '3',
    input: 'steps',
    type: 'int',
    label: 'ステップ数',
    default: 20,
    minimum: 1,
    maximum: 50,
    step: null,
    choices: null,
    maxLength: null,
  }
  const disabledRow: ExposedParamRow = { ...enabledRow, enabled: false, name: 'cfg', node: '3', input: 'cfg' }

  it('trims the name and only includes enabled exposed params', () => {
    const body = buildWorkflowCreateRequest({
      name: '  My Workflow  ',
      operation: 'generate',
      template,
      bindings,
      exposedRows: [enabledRow, disabledRow],
    })
    expect(body.name).toBe('My Workflow')
    expect(body.operation).toBe('generate')
    expect(body.template).toBe(template)
    expect(body.bindings.prompt).toEqual({ node: '1', input: 'text' })
    expect(body.exposed_params).toHaveLength(1)
    expect(body.exposed_params?.[0].name).toBe('steps')
  })

  it('always includes final_prompt in bindings (null when none), so an update does not drop it', () => {
    const none = buildWorkflowUpdateRequest({ name: 'x', operation: 'generate', template, bindings, exposedRows: [] })
    expect(none.bindings?.final_prompt).toBeNull()
    expect(Object.keys(none.bindings ?? {})).toContain('final_prompt')
    const chosen = buildWorkflowUpdateRequest({
      name: 'x',
      operation: 'generate',
      template,
      bindings: { ...bindings, finalPrompt: '472' },
      exposedRows: [],
    })
    expect(chosen.bindings?.final_prompt).toBe('472')
  })

  it('buildWorkflowUpdateRequest produces the same shape as create', () => {
    const args = { name: 'x', operation: 'edit' as const, template, bindings, exposedRows: [enabledRow] }
    expect(buildWorkflowUpdateRequest(args)).toEqual(buildWorkflowCreateRequest(args))
  })
})

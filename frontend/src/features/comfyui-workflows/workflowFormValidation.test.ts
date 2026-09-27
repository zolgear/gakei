import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import { validateWorkflowForm, type WorkflowFormInput } from './workflowFormValidation'
import { EMPTY_BINDINGS_FORM, type BindingsFormState } from './bindingsForm'
import type { ExposedParamRow } from './exposedParamsForm'

function baseInput(overrides: Partial<WorkflowFormInput> = {}): WorkflowFormInput {
  return {
    name: 'テスト用ワークフロー',
    operation: 'generate',
    hasTemplate: true,
    bindings: {
      ...EMPTY_BINDINGS_FORM,
      prompt: { node: '1', input: 'text' },
      outputs: ['3'],
    } satisfies BindingsFormState,
    exposedRows: [],
    ...overrides,
  }
}

describe('validateWorkflowForm', () => {
  it('passes for a minimal valid form', () => {
    expect(validateWorkflowForm(baseInput())).toEqual([])
  })

  it('requires a name and a selected template file', () => {
    const errors = validateWorkflowForm(baseInput({ name: '  ', hasTemplate: false }))
    expect(errors).toContain('名前を入力してください')
    expect(errors).toContain('ワークフローの JSON ファイルを選択してください')
  })

  it('flags a binding sharing (node, input) with an enabled exposed param', () => {
    const row: ExposedParamRow = {
      enabled: true,
      name: 'my_prompt',
      node: '1',
      input: 'text',
      type: 'text',
      label: 'text',
      default: null,
      minimum: null,
      maximum: null,
      step: null,
      choices: null,
      maxLength: null,
    }
    const errors = validateWorkflowForm(baseInput({ exposedRows: [row] }))
    expect(errors.some((e) => e.includes('重複'))).toBe(true)
  })

  describe('en locale', () => {
    afterEach(() => setLocale('ja'))

    it('requires a name and a selected template file, in English', () => {
      setLocale('en')
      const errors = validateWorkflowForm(baseInput({ name: '  ', hasTemplate: false }))
      expect(errors).toContain('Please enter a name.')
      expect(errors).toContain('Please choose the workflow JSON file.')
    })
  })

  it('does not flag a disabled exposed param sharing (node, input) with a binding', () => {
    const row: ExposedParamRow = {
      enabled: false,
      name: 'my_prompt',
      node: '1',
      input: 'text',
      type: 'text',
      label: 'text',
      default: null,
      minimum: null,
      maximum: null,
      step: null,
      choices: null,
      maxLength: null,
    }
    expect(validateWorkflowForm(baseInput({ exposedRows: [row] }))).toEqual([])
  })
})

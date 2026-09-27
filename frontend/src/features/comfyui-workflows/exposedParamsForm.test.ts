import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../../i18n'
import {
  buildExposedParamRows,
  exposedParamRowToApi,
  formatChoicesInput,
  formatDefaultInput,
  parseChoicesInput,
  parseDefaultInput,
  parseNumberInput,
  reconcileExposedParamsOnReplace,
  validateExposedParamName,
  validateExposedParamRows,
  type ExposedParamRow,
} from './exposedParamsForm'
import type { ComfyExposedParam, ComfyNodeInfo } from '../../api/client'

function makeParam(overrides: Partial<ComfyExposedParam> = {}): ComfyExposedParam {
  return {
    name: 'steps',
    node: '3',
    input: 'steps',
    type: 'int',
    label: 'steps',
    default: 20,
    ...overrides,
  }
}

function makeRow(overrides: Partial<ExposedParamRow> = {}): ExposedParamRow {
  return {
    enabled: false,
    name: 'steps',
    node: '3',
    input: 'steps',
    type: 'int',
    label: 'steps',
    default: 20,
    minimum: null,
    maximum: null,
    step: null,
    choices: null,
    maxLength: null,
    ...overrides,
  }
}

describe('buildExposedParamRows', () => {
  it('unchecks candidates with no saved match', () => {
    const rows = buildExposedParamRows([makeParam()], [])
    expect(rows).toEqual([expect.objectContaining({ enabled: false, name: 'steps' })])
  })

  it('checks and uses the saved config when a candidate matches by (node, input)', () => {
    const saved = makeParam({ name: 'my_steps', label: 'ステップ数', minimum: 1, maximum: 50 })
    const rows = buildExposedParamRows([makeParam()], [saved])
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ enabled: true, name: 'my_steps', label: 'ステップ数', minimum: 1, maximum: 50 })
  })

  it('appends saved params that are not in the candidate list', () => {
    const saved = makeParam({ node: '9', input: 'cfg', name: 'cfg' })
    const rows = buildExposedParamRows([makeParam()], [saved])
    expect(rows).toHaveLength(2)
    expect(rows[1]).toMatchObject({ enabled: true, name: 'cfg', node: '9', input: 'cfg' })
  })
})

describe('exposedParamRowToApi', () => {
  it('only keeps choices for enum and max_length for text', () => {
    const enumRow = makeRow({ type: 'enum', choices: ['a', 'b'], maxLength: 10 })
    expect(exposedParamRowToApi(enumRow).choices).toEqual(['a', 'b'])
    expect(exposedParamRowToApi(enumRow).max_length).toBeNull()

    const textRow = makeRow({ type: 'text', choices: ['a'], maxLength: 10 })
    expect(exposedParamRowToApi(textRow).choices).toBeNull()
    expect(exposedParamRowToApi(textRow).max_length).toBe(10)
  })
})

describe('reconcileExposedParamsOnReplace', () => {
  const nodes: ComfyNodeInfo[] = [
    { id: '3', class_type: 'KSampler', title: null, inputs: [{ name: 'steps', value: 20, linked: false }] },
  ]

  it('drops rows whose (node, input) disappeared and reports enabled ones', () => {
    const rows = [makeRow({ enabled: true }), makeRow({ node: '9', input: 'cfg', name: 'cfg', enabled: true })]
    const { next, clearedNames } = reconcileExposedParamsOnReplace(rows, [], nodes)
    expect(next).toHaveLength(1)
    expect(next[0].name).toBe('steps')
    expect(clearedNames).toEqual(['cfg'])
  })

  it('does not report disabled rows as cleared, but still drops them', () => {
    const rows = [makeRow({ node: '9', input: 'cfg', name: 'cfg', enabled: false })]
    const { next, clearedNames } = reconcileExposedParamsOnReplace(rows, [], nodes)
    expect(next).toEqual([])
    expect(clearedNames).toEqual([])
  })

  it('adds new candidates (unchecked) that are not already present', () => {
    const newCandidate = makeParam({ node: '3', input: 'steps', name: 'steps' })
    const { next } = reconcileExposedParamsOnReplace([], [newCandidate], nodes)
    expect(next).toEqual([expect.objectContaining({ enabled: false, name: 'steps' })])
  })
})

describe('number/default/choices parsing', () => {
  it('parseNumberInput', () => {
    expect(parseNumberInput('')).toBeNull()
    expect(parseNumberInput('  ')).toBeNull()
    expect(parseNumberInput('3.5')).toBe(3.5)
    expect(parseNumberInput('abc')).toBeNull()
  })

  it('parseDefaultInput truncates ints, keeps floats, passes text through', () => {
    expect(parseDefaultInput('int', '3.9')).toBe(3)
    expect(parseDefaultInput('float', '3.9')).toBe(3.9)
    expect(parseDefaultInput('text', 'hello')).toBe('hello')
    expect(parseDefaultInput('int', '')).toBeNull()
  })

  it('formatDefaultInput', () => {
    expect(formatDefaultInput(null)).toBe('')
    expect(formatDefaultInput(undefined)).toBe('')
    expect(formatDefaultInput(3)).toBe('3')
    expect(formatDefaultInput('x')).toBe('x')
  })

  it('parseChoicesInput / formatChoicesInput round-trip, dropping blanks', () => {
    expect(parseChoicesInput('a, b,, c ')).toEqual(['a', 'b', 'c'])
    expect(formatChoicesInput(['a', 'b'])).toBe('a, b')
    expect(formatChoicesInput(null)).toBe('')
  })
})

describe('validateExposedParamName', () => {
  it('accepts a valid name', () => {
    expect(validateExposedParamName('my_steps')).toBeNull()
  })

  it('rejects bad formats, comfyui_ prefix, and reserved words', () => {
    expect(validateExposedParamName('MySteps')).not.toBeNull()
    expect(validateExposedParamName('1steps')).not.toBeNull()
    expect(validateExposedParamName('comfyui_seed')).not.toBeNull()
    expect(validateExposedParamName('seed')).not.toBeNull()
    expect(validateExposedParamName('model')).not.toBeNull()
  })
})

describe('validateExposedParamRows', () => {
  it('ignores disabled rows entirely', () => {
    expect(validateExposedParamRows([makeRow({ enabled: false, name: 'Bad Name' })])).toEqual([])
  })

  it('reports invalid names on enabled rows', () => {
    const errors = validateExposedParamRows([makeRow({ enabled: true, name: 'Bad Name' })])
    expect(errors).toHaveLength(1)
  })

  it('reports duplicate names among enabled rows', () => {
    const rows = [
      makeRow({ enabled: true, name: 'steps', node: '3', input: 'steps' }),
      makeRow({ enabled: true, name: 'steps', node: '4', input: 'steps' }),
    ]
    const errors = validateExposedParamRows(rows)
    expect(errors.some((e) => e.includes('重複'))).toBe(true)
  })

  describe('en locale', () => {
    afterEach(() => setLocale('ja'))

    it('interpolates the duplicate name into the English error', () => {
      setLocale('en')
      const rows = [
        makeRow({ enabled: true, name: 'steps', node: '3', input: 'steps' }),
        makeRow({ enabled: true, name: 'steps', node: '4', input: 'steps' }),
      ]
      const errors = validateExposedParamRows(rows)
      expect(errors).toContain('Duplicate exposed parameter name: steps')
    })
  })
})

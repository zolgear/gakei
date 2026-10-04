import { describe, expect, it } from 'vitest'
import type { RunTextOutput } from '../../api/client'
import {
  FINAL_PROMPT_COLLAPSE_CHARS,
  findFinalPrompt,
  finalPromptNodeLabel,
  isFinalPromptCollapsible,
} from './finalPrompt'

function item(overrides: Partial<RunTextOutput> = {}): RunTextOutput {
  return { role: 'final_prompt', node_id: '472', class_type: 'PreviewAny', title: null, text: 'a cat', truncated: false, ...overrides }
}

describe('findFinalPrompt', () => {
  it('returns null for missing or empty lists', () => {
    expect(findFinalPrompt(null)).toBeNull()
    expect(findFinalPrompt(undefined)).toBeNull()
    expect(findFinalPrompt([])).toBeNull()
  })

  it('picks only role=final_prompt and ignores other roles', () => {
    const other = item({ role: 'debug', text: 'ignored' })
    const final = item({ text: 'final' })
    expect(findFinalPrompt([other, final])?.text).toBe('final')
    expect(findFinalPrompt([other])).toBeNull()
  })
})

describe('finalPromptNodeLabel', () => {
  it('formats "id: class_type" and adds the title when present', () => {
    expect(finalPromptNodeLabel(item())).toBe('472: PreviewAny')
    expect(finalPromptNodeLabel(item({ title: 'PE out' }))).toBe('472: PreviewAny — PE out')
  })

  it('handles missing parts', () => {
    expect(finalPromptNodeLabel(item({ class_type: null }))).toBe('472: ?')
    expect(finalPromptNodeLabel(item({ node_id: null }))).toBe('PreviewAny')
    expect(finalPromptNodeLabel(item({ node_id: null, class_type: null }))).toBeNull()
  })
})

describe('isFinalPromptCollapsible', () => {
  it('does not collapse short text', () => {
    expect(isFinalPromptCollapsible('one\ntwo\nthree\nfour')).toBe(false)
  })

  it('collapses when there are more lines than shown', () => {
    expect(isFinalPromptCollapsible('1\n2\n3\n4\n5')).toBe(true)
  })

  it('collapses long single-line text', () => {
    expect(isFinalPromptCollapsible('x'.repeat(FINAL_PROMPT_COLLAPSE_CHARS + 1))).toBe(true)
    expect(isFinalPromptCollapsible('x'.repeat(FINAL_PROMPT_COLLAPSE_CHARS))).toBe(false)
  })
})

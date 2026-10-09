import { describe, expect, it } from 'vitest'
import type { RunTextOutput } from '../../api/client'
import {
  FINAL_PROMPT_COLLAPSE_CHARS,
  baselineNegativePrompt,
  findFinalPrompt,
  finalPromptEntriesForOutput,
  finalPromptEntriesForRun,
  selectFinalPromptEntries,
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

// SD WebUI が出力ごとに記録する展開後のプロンプト(ADR-0038 7章)。
function sd(role: string, outputIndex: number, text: string): RunTextOutput {
  return { role, output_index: outputIndex, text, truncated: false }
}

const sdOutputs: RunTextOutput[] = [
  sd('final_prompt', 0, 'a red cat'),
  sd('final_negative_prompt', 0, 'blurry'),
  sd('final_prompt', 1, 'a blue cat'),
  sd('final_negative_prompt', 1, 'blurry'),
  sd('final_prompt', 2, 'a {red|blue} cat'),
  sd('final_negative_prompt', 2, 'ugly'),
]
const baseline = { prompt: 'a {red|blue} cat', negativePrompt: 'blurry' }

describe('findFinalPrompt per output', () => {
  it('prefers the item whose output_index matches', () => {
    expect(findFinalPrompt(sdOutputs, 1)?.text).toBe('a blue cat')
    expect(findFinalPrompt(sdOutputs, 1, 'final_negative_prompt')?.text).toBe('blurry')
  })

  it('falls back to the item for all outputs (output_index null)', () => {
    const comfy = [item({ text: 'final' })]
    expect(findFinalPrompt(comfy, 3)?.text).toBe('final')
    expect(findFinalPrompt([item({ output_index: null, text: 'x' })], 0)?.text).toBe('x')
  })

  it('does not pick another output\'s item', () => {
    expect(findFinalPrompt(sdOutputs, 9)).toBeNull()
    expect(findFinalPrompt(sdOutputs, null)).toBeNull()
    expect(findFinalPrompt(sdOutputs)).toBeNull()
  })
})

describe('finalPromptEntriesForOutput', () => {
  it('shows the expanded prompt and hides the negative that equals the run value', () => {
    const entries = finalPromptEntriesForOutput(sdOutputs, 0, baseline)
    expect(entries.map((e) => [e.kind, e.item.text])).toEqual([['expanded', 'a red cat']])
  })

  it('hides the prompt equal to the run prompt and shows a different negative', () => {
    const entries = finalPromptEntriesForOutput(sdOutputs, 2, baseline)
    expect(entries.map((e) => [e.kind, e.item.text])).toEqual([['expandedNegative', 'ugly']])
  })

  it('ignores surrounding whitespace when comparing', () => {
    const outputs = [sd('final_prompt', 0, '  a {red|blue} cat\n')]
    expect(finalPromptEntriesForOutput(outputs, 0, baseline)).toEqual([])
  })

  it('does not show negatives while the run negative is unknown', () => {
    const entries = finalPromptEntriesForOutput(sdOutputs, 2, { prompt: baseline.prompt })
    expect(entries).toEqual([])
  })

  it('always shows the ComfyUI final prompt, even when equal to the run prompt', () => {
    const comfy = [item({ text: 'a {red|blue} cat' })]
    const entries = finalPromptEntriesForOutput(comfy, 0, baseline)
    expect(entries.map((e) => e.kind)).toEqual(['final'])
  })
})

describe('finalPromptEntriesForRun', () => {
  it('lists only differing items, by output then prompt before negative', () => {
    const shuffled = [sdOutputs[5], sdOutputs[2], sdOutputs[0], sdOutputs[1], sdOutputs[3], sdOutputs[4]]
    const entries = finalPromptEntriesForRun(shuffled, baseline)
    expect(entries.map((e) => [e.kind, e.outputIndex])).toEqual([
      ['expanded', 0],
      ['expanded', 1],
      ['expandedNegative', 2],
    ])
  })

  it('returns nothing when every output equals the run values', () => {
    const outputs = [sd('final_prompt', 0, 'p'), sd('final_negative_prompt', 0, ''), sd('final_prompt', 1, 'p')]
    expect(finalPromptEntriesForRun(outputs, { prompt: 'p', negativePrompt: '' })).toEqual([])
  })

  it('keeps the ComfyUI final prompt first', () => {
    const outputs = [sd('final_prompt', 0, 'other'), item({ text: 'final' })]
    expect(finalPromptEntriesForRun(outputs, { prompt: 'p' }).map((e) => e.kind)).toEqual(['final', 'expanded'])
  })
})

describe('selectFinalPromptEntries', () => {
  it('shows only the ComfyUI final prompt when no run prompt is given (old callers)', () => {
    const outputs = [...sdOutputs, item({ text: 'final' })]
    expect(selectFinalPromptEntries({ textOutputs: outputs }).map((e) => e.kind)).toEqual(['final'])
  })

  it('selects one output when outputIndex is given', () => {
    const entries = selectFinalPromptEntries({ textOutputs: sdOutputs, outputIndex: 1, ...baseline })
    expect(entries.map((e) => e.item.text)).toEqual(['a blue cat'])
  })
})

describe('baselineNegativePrompt', () => {
  it('reads params.negative_prompt, or empty', () => {
    expect(baselineNegativePrompt({ negative_prompt: 'ugly' })).toBe('ugly')
    expect(baselineNegativePrompt({})).toBe('')
    expect(baselineNegativePrompt(null)).toBe('')
  })
})

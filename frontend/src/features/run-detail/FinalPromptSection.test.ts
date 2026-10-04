import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { RunTextOutput } from '../../api/client'
import { msg } from '../../i18n'
import { RunPromptActions } from '../workspace/RunPromptActions'
import { FinalPromptSection } from './FinalPromptSection'

// 画面のテストの仕組み(jsdom)は入れていないので、静的な HTML に描いて属性を確かめる。
const textOutputs: RunTextOutput[] = [
  { role: 'final_prompt', node_id: '472', class_type: 'PreviewAny', title: null, text: 'a cat', truncated: false },
]

function buttons(html: string): string[] {
  return html.match(/<button[^>]*>/g) ?? []
}

describe('FinalPromptSection icon buttons', () => {
  it('renders copy as an icon button named by aria-label and title', () => {
    const t = msg()
    const html = renderToStaticMarkup(createElement(FinalPromptSection, { textOutputs }))
    const copy = buttons(html).find((b) => b.includes(`aria-label="${t.finalPrompt.copy}"`))
    expect(copy).toBeDefined()
    expect(copy).toContain(`title="${t.finalPrompt.copy}"`)
    expect(html).not.toContain(`>${t.finalPrompt.copy}<`)
    // 結果を知らせる aria-live の領域は最初は空。
    expect(html).toMatch(/aria-live="polite"[^>]*><\/span>/)
  })

  it('renders insert and replace as icon buttons before copy', () => {
    const t = msg()
    const rpa = t.workspace.runPromptActions
    const html = renderToStaticMarkup(
      createElement(FinalPromptSection, {
        textOutputs,
        renderActions: (text: string) =>
          createElement(RunPromptActions, { prompt: text, currentPrompt: '', insertPrompt: () => {} }),
      }),
    )
    const labels = buttons(html).map((b) => /aria-label="([^"]*)"/.exec(b)?.[1])
    expect(labels).toEqual([rpa.insertIntoPrompt, rpa.replacePrompt, t.finalPrompt.copy])
    expect(html).not.toContain(`>${rpa.insertIntoPrompt}<`)
    expect(html).not.toContain(`>${rpa.replacePrompt}<`)
  })
})

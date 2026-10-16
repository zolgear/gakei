import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import type { ModVerdictView, RunModInvocationView } from '../../api/client'
import { msg } from '../../i18n'
import { USER_PREFERENCES_QUERY_KEY } from '../settings/userPreferences'
import { AssetThumb } from '../content-rating/AssetThumb'
import { AssetVerdictsSection, WithheldNotice } from './AssetVerdictsSection'
import { RunModChecksSection } from './RunModChecksSection'
import { VerdictList } from './VerdictList'
import { WithheldPlaceholder } from './WithheldPlaceholder'

// 画面のテストの仕組み(jsdom)は入れていないので、描いた HTML を確かめる(AssetThumb.test.ts と同じ)。

function withQuery(node: ReturnType<typeof createElement>) {
  const client = new QueryClient()
  client.setQueryData(USER_PREFERENCES_QUERY_KEY, {})
  return renderToStaticMarkup(createElement(QueryClientProvider, { client }, node))
}

function verdict(v: ModVerdictView['verdict'], extra: Partial<ModVerdictView> = {}): ModVerdictView {
  return {
    id: `v-${v}`,
    mod_kind: 'webhook',
    mod_id: 'w1',
    mod_name: 'Checker',
    verdict: v,
    propagated: false,
    created_at: '2026-10-16T00:00:00Z',
    ...extra,
  }
}

const m = msg().mods

describe('WithheldPlaceholder', () => {
  it('アイコンと「画像なし(拒否)」を出し、画像は読みに行かない', () => {
    const html = renderToStaticMarkup(createElement(WithheldPlaceholder, { className: 'thumb' }))
    expect(html).toContain(m.withheld)
    expect(html).toContain('role="img"')
    expect(html).toContain('class="')
    expect(html).toContain('thumb')
    expect(html).not.toContain('<img')
  })

  it('判定の label を添える(説明にも入れる)', () => {
    const html = renderToStaticMarkup(createElement(WithheldPlaceholder, { label: '類似の疑い' }))
    expect(html).toContain('類似の疑い')
    expect(html).toContain('aria-label="画像なし(拒否): 類似の疑い"')
  })

  it('message は大きな枠(canvas)のときだけ出す', () => {
    const thumb = renderToStaticMarkup(createElement(WithheldPlaceholder, { message: '詳細な理由' }))
    const canvas = renderToStaticMarkup(createElement(WithheldPlaceholder, { message: '詳細な理由', variant: 'canvas' }))
    expect(thumb).not.toContain('詳細な理由')
    expect(canvas).toContain('詳細な理由')
  })
})

describe('AssetThumb の画像なし', () => {
  it('withheld なら <img> を描かず置き換えの表示にする(className はそのまま)', () => {
    const html = withQuery(
      createElement(AssetThumb, { assetId: 'a1', rating: 'x', withheld: true, className: 'tileImage', alt: '' }),
    )
    expect(html).not.toContain('<img')
    expect(html).not.toContain('/api/assets/a1/content')
    expect(html).toContain('tileImage')
    expect(html).toContain('data-withheld="true"')
    // マスクとバッジも出さない(見せる画像が無い)。
    expect(html).not.toContain('data-rating-masked')
  })

  it('withheld でなければ今までどおり <img>', () => {
    const html = withQuery(createElement(AssetThumb, { assetId: 'a1', rating: 'g', alt: '' }))
    expect(html).toContain('<img')
    expect(html).not.toContain('data-withheld')
  })
})

describe('VerdictList', () => {
  it('印、Mod の名前、label、score、message、reference を出す', () => {
    const html = renderToStaticMarkup(
      createElement(VerdictList, {
        verdicts: [
          verdict('flag', { label: '類似の疑い', score: 0.82, message: '既存作品と似ています', reference: 'CASE-12' }),
        ],
      }),
    )
    expect(html).toContain(m.verdicts.flag)
    expect(html).toContain('Checker')
    expect(html).toContain('類似の疑い')
    expect(html).toContain('82%')
    expect(html).toContain('既存作品と似ています')
    expect(html).toContain('CASE-12')
  })

  it('外部へのリンクは新しいタブで、opener と referrer を渡さない', () => {
    const html = renderToStaticMarkup(
      createElement(VerdictList, { verdicts: [verdict('ok', { url: 'https://example.com/r/1' })] }),
    )
    expect(html).toContain('href="https://example.com/r/1"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('http(s) 以外の URL はリンクにしない', () => {
    const html = renderToStaticMarkup(
      createElement(VerdictList, { verdicts: [verdict('ok', { url: 'javascript:alert(1)' })] }),
    )
    expect(html).not.toContain('<a')
  })

  it('同じ内容からの波及なら、そのことを添える', () => {
    const html = renderToStaticMarkup(createElement(VerdictList, { verdicts: [verdict('deny', { propagated: true })] }))
    expect(html).toContain(m.propagated)
  })
})

describe('ビューアの判定の節', () => {
  const base = { content_withheld: false, content_withheld_at: null, content_withheld_verdict: null }

  it('判定が無ければ節を出さない', () => {
    expect(renderToStaticMarkup(createElement(AssetVerdictsSection, { asset: { ...base, verdicts: [] } }))).toBe('')
  })

  it('判定があれば見出しと一覧を出す', () => {
    const html = renderToStaticMarkup(
      createElement(AssetVerdictsSection, { asset: { ...base, verdicts: [verdict('ok')] } }),
    )
    expect(html).toContain(m.verdictsHeading)
    expect(html).toContain(m.verdicts.ok)
  })

  it('画像なしでなければ理由を出さない', () => {
    expect(renderToStaticMarkup(createElement(WithheldNotice, { asset: { ...base, verdicts: [] } }))).toBe('')
  })

  it('画像なしなら、上部にその理由の判定を出す', () => {
    const html = renderToStaticMarkup(
      createElement(WithheldNotice, {
        asset: {
          ...base,
          content_withheld: true,
          content_withheld_at: '2026-10-16T01:00:00Z',
          content_withheld_verdict: verdict('deny', { label: '侵害の疑い' }),
          verdicts: [],
        },
      }),
    )
    expect(html).toContain(m.withheld)
    expect(html).toContain('侵害の疑い')
  })
})

describe('Run の詳細の送る前のチェック', () => {
  function inv(extra: Partial<RunModInvocationView>): RunModInvocationView {
    return {
      id: 'i1',
      mod_kind: 'webhook',
      mod_id: 'w1',
      mod_name: 'Gate',
      event: 'ai.before_send',
      purpose: 'image_run',
      outcome: 'allow',
      duration_ms: 34,
      created_at: '2026-10-16T00:00:00Z',
      ...extra,
    }
  }

  it('記録が無ければ何も出さない', () => {
    expect(
      renderToStaticMarkup(createElement(RunModChecksSection, { run: { verdicts: [], mod_invocations: [] } })),
    ).toBe('')
  })

  it('許可・拒否、理由、reference、時間を出す', () => {
    const html = renderToStaticMarkup(
      createElement(RunModChecksSection, {
        run: {
          verdicts: [],
          mod_invocations: [inv({ id: 'a', outcome: 'deny', reason: '禁止語を含む', reference: 'R-9', duration_ms: 1530 })],
        },
      }),
    )
    expect(html).toContain(m.checksHeading)
    expect(html).toContain('Gate')
    expect(html).toContain(m.checkOutcomes.deny)
    expect(html).toContain('禁止語を含む')
    expect(html).toContain('R-9')
    expect(html).toContain('1.5 s')
  })

  it('失敗したときは、実際に止めたか通したかを添える', () => {
    const blocked = renderToStaticMarkup(
      createElement(RunModChecksSection, {
        run: { mod_invocations: [inv({ outcome: 'timeout', effective: 'deny' })] },
      }),
    )
    const passed = renderToStaticMarkup(
      createElement(RunModChecksSection, {
        run: { mod_invocations: [inv({ outcome: 'error', effective: 'allow' })] },
      }),
    )
    expect(blocked).toContain(m.checkOutcomes.failed)
    expect(blocked).toContain(m.effectiveDeny)
    expect(passed).toContain(m.effectiveAllow)
  })

  it('Run の判定も出す', () => {
    const html = renderToStaticMarkup(
      createElement(RunModChecksSection, { run: { verdicts: [verdict('flag', { label: '要確認の理由' })] } }),
    )
    expect(html).toContain(m.verdictsHeading)
    expect(html).toContain('要確認の理由')
    expect(html).not.toContain(m.checksHeading)
  })
})

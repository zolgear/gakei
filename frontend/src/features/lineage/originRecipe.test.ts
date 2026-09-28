import { describe, expect, it } from 'vitest'
import {
  buildOriginFormState,
  extractOriginRunInfo,
  extractRunInfoFromEmbeddedNode,
  originRecipeDisabledReason,
} from './originRecipe'
import type { CapabilitiesResponse, ProviderEntry } from '../../api/client'

const sizeConstraints = {
  multiple_of: 16,
  max_long_edge: 3840,
  min_total_pixels: 655_360,
  max_total_pixels: 8_294_400,
  min_aspect_ratio: 1 / 3,
  max_aspect_ratio: 3,
  allow_auto: true,
}

function provider(overrides: Partial<ProviderEntry> & Pick<ProviderEntry, 'provider' | 'models'>): ProviderEntry {
  return {
    label: overrides.provider,
    default_model: '',
    default_size: null,
    size: sizeConstraints,
    incompatible_pairs: [],
    conditional_params: [],
    prompt_max_length: 32_000,
    n_min: 1,
    n_max: 10,
    partial_images_min: 0,
    partial_images_max: 3,
    max_input_image_bytes: 1,
    max_mask_bytes: 1,
    max_input_images: 16,
    available: true,
    unavailable_reason: null,
    requires_api_key: false,
    supports_pricing: true,
    ...overrides,
  }
}

const caps: CapabilitiesResponse = {
  default_provider: 'openai',
  providers: [
    provider({
      provider: 'openai',
      models: [{ model: 'gpt-image-2.5', label: 'GPT Image 2.5', description: '', quality_choices: [], operations: [] }],
    }),
  ],
}

describe('extractOriginRunInfo', () => {
  it('妥当な meta.run を読み取る', () => {
    const meta = {
      schema: 'gakei.lineage/1',
      run: {
        provider: 'openai',
        model: 'gpt-image-2.5',
        prompt: '山の風景',
        params: { quality: 'high', n: 2 },
      },
    }
    expect(extractOriginRunInfo(meta)).toEqual({
      provider: 'openai',
      model: 'gpt-image-2.5',
      prompt: '山の風景',
      params: { quality: 'high', n: 2 },
    })
  })

  it('run が無い(sketch の source_asset のみ)なら null', () => {
    const meta = { schema: 'gakei.lineage/1', source_asset: { id: 'a', sha256: 'b' } }
    expect(extractOriginRunInfo(meta)).toBeNull()
  })

  it('meta 自体がオブジェクトでなければ null', () => {
    expect(extractOriginRunInfo(null)).toBeNull()
    expect(extractOriginRunInfo('broken')).toBeNull()
    expect(extractOriginRunInfo(42)).toBeNull()
  })

  it('run がオブジェクトでなければ null', () => {
    expect(extractOriginRunInfo({ run: 'not-an-object' })).toBeNull()
    expect(extractOriginRunInfo({ run: null })).toBeNull()
    expect(extractOriginRunInfo({ run: ['x'] })).toBeNull()
  })

  it('provider/model/prompt の型が違えば個別に null にする(壊れた自己申告)', () => {
    const meta = { run: { provider: 123, model: undefined, prompt: { nested: true }, params: 'nope' } }
    expect(extractOriginRunInfo(meta)).toEqual({ provider: null, model: null, prompt: null, params: {} })
  })

  it('provider/model が空文字なら null 扱い', () => {
    const meta = { run: { provider: '', model: '', prompt: '' } }
    expect(extractOriginRunInfo(meta)).toEqual({ provider: null, model: null, prompt: '', params: {} })
  })

  it('params のうちオブジェクト・配列・null の値は捨て、プリミティブだけ残す', () => {
    const meta = {
      run: {
        provider: 'openai',
        model: 'gpt-image-2.5',
        prompt: 'x',
        params: { quality: 'high', nested: { a: 1 }, list: [1, 2], nothing: null, ok: 3, flag: true },
      },
    }
    expect(extractOriginRunInfo(meta)?.params).toEqual({ quality: 'high', ok: 3, flag: true })
  })

  it('v2(gakei.lineage/2): root への output エッジの source を Run ノードとして読む', () => {
    const meta = {
      schema: 'gakei.lineage/2',
      instance: 'inst-a',
      root: 'asset-1',
      nodes: [
        { type: 'asset', id: 'asset-1', instance: 'inst-a', sha256: 'x', kind: 'generated' },
        {
          type: 'run',
          id: 'run-1',
          instance: 'inst-a',
          provider: 'openai',
          model: 'gpt-image-2.5',
          operation: 'generate',
          prompt: '山の風景',
          params: { quality: 'high' },
          status: 'succeeded',
        },
      ],
      edges: [{ source: 'run-1', target: 'asset-1', kind: 'output', output_index: 0 }],
      truncated: false,
    }
    expect(extractOriginRunInfo(meta)).toEqual({
      provider: 'openai',
      model: 'gpt-image-2.5',
      prompt: '山の風景',
      params: { quality: 'high' },
    })
  })

  it('v2: root を生んだ Run が無ければ null(取り込んだだけの upload が起点など)', () => {
    const meta = {
      schema: 'gakei.lineage/2',
      root: 'asset-1',
      nodes: [{ type: 'asset', id: 'asset-1', instance: 'inst-a', sha256: 'x', kind: 'upload' }],
      edges: [],
      truncated: false,
    }
    expect(extractOriginRunInfo(meta)).toBeNull()
  })

  it('v2: nodes/edges/root の形が崩れていれば null', () => {
    expect(extractOriginRunInfo({ schema: 'gakei.lineage/2', nodes: 'not-an-array', root: 'a', edges: [] })).toBeNull()
    expect(extractOriginRunInfo({ schema: 'gakei.lineage/2', nodes: [], root: 'a', edges: 'not-an-array' })).toBeNull()
    expect(extractOriginRunInfo({ schema: 'gakei.lineage/2', nodes: [], root: 42, edges: [] })).toBeNull()
    // nodes の要素が壊れていても例外を出さず null 扱い
    expect(
      extractOriginRunInfo({
        root: 'asset-1',
        nodes: [null, 'broken', 42, { type: 'run' }],
        edges: [null, { kind: 'output', target: 'asset-1' }],
      }),
    ).toBeNull()
  })
})

describe('extractRunInfoFromEmbeddedNode', () => {
  it('埋め込み Run ノードの embedded_detail をそのまま読む', () => {
    const detail = {
      type: 'run',
      id: 'run-1',
      instance: 'inst-b',
      provider: 'openai',
      model: 'gpt-image-2.5',
      operation: 'edit',
      prompt: '猫',
      params: { quality: 'high', n: 2 },
      status: 'succeeded',
      finished_at: '2026-09-24T00:00:00Z',
    }
    expect(extractRunInfoFromEmbeddedNode(detail)).toEqual({
      provider: 'openai',
      model: 'gpt-image-2.5',
      prompt: '猫',
      params: { quality: 'high', n: 2 },
    })
  })

  it('オブジェクトでない、または null なら null', () => {
    expect(extractRunInfoFromEmbeddedNode(null)).toBeNull()
    expect(extractRunInfoFromEmbeddedNode('broken')).toBeNull()
    expect(extractRunInfoFromEmbeddedNode(['x'])).toBeNull()
  })

  it('provider/model の型が違えば個別に null にする(壊れた自己申告)', () => {
    expect(extractRunInfoFromEmbeddedNode({ provider: 1, model: undefined, prompt: 2 })).toEqual({
      provider: null,
      model: null,
      prompt: null,
      params: {},
    })
  })
})

describe('originRecipeDisabledReason', () => {
  it('info が null なら無効', () => {
    expect(originRecipeDisabledReason(null, caps)).not.toBeNull()
  })

  it('provider/model が読み取れないなら無効', () => {
    expect(
      originRecipeDisabledReason({ provider: null, model: 'gpt-image-2.5', prompt: 'x', params: {} }, caps),
    ).not.toBeNull()
  })

  it('caps が未読み込みなら無効(読み込み中)', () => {
    expect(
      originRecipeDisabledReason({ provider: 'openai', model: 'gpt-image-2.5', prompt: 'x', params: {} }, undefined),
    ).not.toBeNull()
  })

  it('今の capabilities に無いモデルなら無効(別インスタンス・無効化されたプロバイダー等)', () => {
    expect(
      originRecipeDisabledReason({ provider: 'comfyui', model: 'unknown-workflow', prompt: 'x', params: {} }, caps),
    ).not.toBeNull()
  })

  it('provider/model が今の capabilities にあれば有効', () => {
    expect(
      originRecipeDisabledReason({ provider: 'openai', model: 'gpt-image-2.5', prompt: 'x', params: {} }, caps),
    ).toBeNull()
  })
})

describe('buildOriginFormState', () => {
  it('provider/model/prompt/params をそのままフォーム状態にし、inputs は常に空・グループはなし', () => {
    const state = buildOriginFormState({
      provider: 'openai',
      model: 'gpt-image-2.5',
      prompt: '山の風景',
      params: { quality: 'high' },
    })
    expect(state).toEqual({
      provider: 'openai',
      model: 'gpt-image-2.5',
      prompt: '山の風景',
      params: { quality: 'high' },
      inputs: [],
      assetGroupId: null,
    })
  })

  it('comfyui_* は paramsForRerun により取り除かれ、comfyui_seed だけ seed として残る', () => {
    const state = buildOriginFormState({
      provider: 'comfyui',
      model: 'wf-1',
      prompt: 'x',
      params: {
        comfyui_workflow: 'wf-uuid',
        comfyui_seed: 12345,
        comfyui_mask_mode: 'inpaint',
        negative_prompt: 'blurry',
      },
    })
    expect(state.params).toEqual({ negative_prompt: 'blurry', seed: 12345 })
  })

  it('provider/model が null(未検証・欠落)でも例外を出さず空文字になる', () => {
    const state = buildOriginFormState({ provider: null, model: null, prompt: null, params: {} })
    expect(state).toEqual({ provider: '', model: '', prompt: '', params: {}, inputs: [], assetGroupId: null })
  })

  it('渡したグループ(最後に選んだグループ)をそのまま使う', () => {
    const state = buildOriginFormState({ provider: 'fake', model: 'm', prompt: 'x', params: {} }, 'g1')
    expect(state.assetGroupId).toBe('g1')
  })
})

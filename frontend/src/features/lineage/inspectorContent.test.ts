import { describe, expect, it } from 'vitest'
import { resolveInspectorContent, type InspectorGraphNode } from './inspectorContent'
import type { LineageNode } from '../../api/client'

const nodes: InspectorGraphNode[] = [
  { id: 'asset-1', type: 'asset' },
  { id: 'run-1', type: 'run' },
]

function embeddedAssetNode(id: string): LineageNode {
  return {
    id,
    type: 'asset',
    depth: -1,
    deleted: false,
    embedded: true,
    instance: 'other-instance',
    resolved_asset_id: null,
    embedded_detail: { type: 'asset', id, sha256: 'abc' },
    asset: { kind: 'generated', width: null, height: null, mime: null, restorable: false },
  }
}

function embeddedRunNode(id: string): LineageNode {
  return {
    id,
    type: 'run',
    depth: -2,
    deleted: false,
    embedded: true,
    instance: 'other-instance',
    embedded_detail: { type: 'run', id, provider: 'openai', model: 'gpt-image-2.5', prompt: 'x' },
    run: { operation: 'generate', model: 'gpt-image-2.5', provider: 'openai', status: 'succeeded', prompt: 'x' },
  }
}

describe('resolveInspectorContent', () => {
  it('nodeId が null なら none', () => {
    expect(resolveInspectorContent(null, nodes)).toEqual({ kind: 'none' })
  })

  it('グラフに存在しない id なら none', () => {
    expect(resolveInspectorContent('missing', nodes)).toEqual({ kind: 'none' })
  })

  it('asset ノードなら asset', () => {
    expect(resolveInspectorContent('asset-1', nodes)).toEqual({ kind: 'asset', id: 'asset-1' })
  })

  it('run ノードなら run', () => {
    expect(resolveInspectorContent('run-1', nodes)).toEqual({ kind: 'run', id: 'run-1' })
  })

  it('embedded な asset ノードなら embedded(ノード本体をそのまま渡す)', () => {
    const node = embeddedAssetNode('embedded-asset-1')
    expect(resolveInspectorContent('embedded-asset-1', [node])).toEqual({ kind: 'embedded', node })
  })

  it('embedded な run ノードなら embedded(ノード本体をそのまま渡す)', () => {
    const node = embeddedRunNode('embedded-run-1')
    expect(resolveInspectorContent('embedded-run-1', [node])).toEqual({ kind: 'embedded', node })
  })
})

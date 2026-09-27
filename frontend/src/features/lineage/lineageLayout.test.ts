import { describe, expect, it } from 'vitest'
import { layoutLineage } from './lineageLayout'
import type { LineageEdge, LineageNode } from '../../api/client'

function assetNode(id: string, depth: number): LineageNode {
  return {
    id,
    type: 'asset',
    depth,
    deleted: false,
    embedded: false,
    asset: { kind: 'generated', width: 1024, height: 1024, mime: 'image/png', restorable: false },
  }
}

function runNode(id: string, depth: number): LineageNode {
  return {
    id,
    type: 'run',
    depth,
    deleted: false,
    embedded: false,
    run: { operation: 'edit', model: 'gpt-image-2.5-sunburst', status: 'succeeded', prompt: 'p', queued_at: '2026-01-01T00:00:00Z' },
  }
}

function outputEdge(runId: string, assetId: string, outputIndex: number): LineageEdge {
  return { source: runId, target: assetId, kind: 'output', output_index: outputIndex, primary: false }
}

function inputEdge(
  assetId: string,
  runId: string,
  position: number,
  primary: boolean,
  role: 'image' | 'mask' | 'reference' = 'image',
): LineageEdge {
  return { source: assetId, target: runId, kind: 'input', role, position, primary }
}

function byId(positions: ReturnType<typeof layoutLineage>) {
  return new Map(positions.map((p) => [p.id, p]))
}

describe('layoutLineage', () => {
  it('起点のみなら1件、depth 0 は y=0', () => {
    const positions = layoutLineage([assetNode('root', 0)], [])
    expect(positions).toEqual([{ id: 'root', x: 0, y: 0 }])
  })

  it('祖先は上(負のy)、子孫は下(正のy)に置く', () => {
    const nodes = [assetNode('root', 0), runNode('parentRun', -1), runNode('childRun', 1)]
    const edges = [outputEdge('parentRun', 'root', 0), inputEdge('root', 'childRun', 0, true)]
    const positions = byId(layoutLineage(nodes, edges))
    expect(positions.get('parentRun')?.y).toBeLessThan(0)
    expect(positions.get('root')?.y).toBe(0)
    expect(positions.get('childRun')?.y).toBeGreaterThan(0)
  })

  it('同じ行は position(祖先側)の昇順に並ぶ', () => {
    const nodes = [
      assetNode('root', 0),
      runNode('run', -1),
      assetNode('inputB', -2),
      assetNode('inputA', -2),
    ]
    const edges = [
      outputEdge('run', 'root', 0),
      inputEdge('inputB', 'run', 1, false),
      inputEdge('inputA', 'run', 0, true),
    ]
    const positions = byId(layoutLineage(nodes, edges))
    const ax = positions.get('inputA')?.x ?? 0
    const bx = positions.get('inputB')?.x ?? 0
    expect(ax).toBeLessThan(bx)
  })

  it('同じ行は output_index(子孫側)の昇順に並ぶ', () => {
    const nodes = [assetNode('root', 0), runNode('run', 1), assetNode('out1', 2), assetNode('out0', 2)]
    const edges = [
      inputEdge('root', 'run', 0, true),
      outputEdge('run', 'out1', 1),
      outputEdge('run', 'out0', 0),
    ]
    const positions = byId(layoutLineage(nodes, edges))
    expect(positions.get('out0')!.x).toBeLessThan(positions.get('out1')!.x)
  })

  it('親の並び順を引き継ぐ(親が右にいれば子も右寄りになりやすい)', () => {
    // root から2つの run(runA, runB)が枝分かれし、それぞれに出力が1つずつある。
    const nodes = [
      assetNode('root', 0),
      runNode('runA', 1),
      runNode('runB', 1),
      assetNode('outA', 2),
      assetNode('outB', 2),
    ]
    const edges = [
      inputEdge('root', 'runA', 0, true),
      inputEdge('root', 'runB', 0, true),
      outputEdge('runA', 'outA', 0),
      outputEdge('runB', 'outB', 0),
    ]
    const positions = byId(layoutLineage(nodes, edges))
    // runA/runB の左右関係と outA/outB の左右関係が一致する(id 順 runA<runB なのでこの順)。
    const runOrder = (positions.get('runA')!.x) < (positions.get('runB')!.x)
    const outOrder = (positions.get('outA')!.x) < (positions.get('outB')!.x)
    expect(outOrder).toBe(runOrder)
  })

  it('同じ行は中央揃えになる(x の平均が概ね0)', () => {
    const nodes = [assetNode('root', 0), runNode('run', -1), assetNode('a', -2), assetNode('b', -2), assetNode('c', -2)]
    const edges = [
      outputEdge('run', 'root', 0),
      inputEdge('a', 'run', 0, true),
      inputEdge('b', 'run', 1, false),
      inputEdge('c', 'run', 2, false),
    ]
    const positions = layoutLineage(nodes, edges).filter((p) => ['a', 'b', 'c'].includes(p.id))
    const sumX = positions.reduce((acc, p) => acc + p.x, 0)
    expect(sumX).toBeCloseTo(0)
  })

  it('columnWidth / rowHeight を指定できる', () => {
    const nodes = [assetNode('root', 0), runNode('run', 1)]
    const edges = [inputEdge('root', 'run', 0, true)]
    const positions = byId(layoutLineage(nodes, edges, { columnWidth: 100, rowHeight: 50 }))
    expect(positions.get('run')?.y).toBe(50)
  })

  it('depth 0 の兄弟出力(n>1 の Run の出力を起点にした場合)は output_index 順に並ぶ', () => {
    // 1つの Run が3つの出力(out0/out1/out2)を持ち、そのうちの1つを起点にした状況。
    // 兄弟出力はすべて depth 0 のノードとして届く(lineage.py の _expand_ancestors)。
    const nodes = [
      runNode('run', -1),
      assetNode('out2', 0),
      assetNode('out0', 0),
      assetNode('out1', 0),
    ]
    const edges = [
      outputEdge('run', 'out0', 0),
      outputEdge('run', 'out1', 1),
      outputEdge('run', 'out2', 2),
    ]
    const positions = byId(layoutLineage(nodes, edges))
    const x0 = positions.get('out0')!.x
    const x1 = positions.get('out1')!.x
    const x2 = positions.get('out2')!.x
    expect(x0).toBeLessThan(x1)
    expect(x1).toBeLessThan(x2)
  })

  it('親に繋がるエッジが見つからないノードでも例外にならない(末尾に安定配置)', () => {
    const nodes = [assetNode('root', 0), assetNode('orphan', -2)]
    const positions = layoutLineage(nodes, [])
    expect(positions).toHaveLength(2)
  })
})

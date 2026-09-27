import { describe, expect, it } from 'vitest'
import {
  findNode,
  inputOptionLabel,
  inputsForNode,
  nodeExists,
  nodeOptionLabel,
  refExistsInNodes,
} from './nodeOptions'
import type { ComfyNodeInfo } from '../../api/client'

const NODES: ComfyNodeInfo[] = [
  {
    id: '3',
    class_type: 'KSampler',
    title: null,
    inputs: [
      { name: 'seed', value: 0, linked: false },
      { name: 'model', value: null, linked: true },
    ],
  },
  {
    id: '6',
    class_type: 'CLIPTextEncode',
    title: 'gakei:prompt',
    inputs: [{ name: 'text', value: '', linked: false }],
  },
]

describe('nodeOptionLabel', () => {
  it('includes the title when present', () => {
    expect(nodeOptionLabel(NODES[1])).toBe('6: CLIPTextEncode — gakei:prompt')
  })

  it('omits the title when absent', () => {
    expect(nodeOptionLabel(NODES[0])).toBe('3: KSampler')
  })
})

describe('inputOptionLabel', () => {
  it('marks linked inputs', () => {
    expect(inputOptionLabel({ name: 'model', value: null, linked: true })).toBe('model(配線)')
    expect(inputOptionLabel({ name: 'seed', value: 0, linked: false })).toBe('seed')
  })
})

describe('findNode / nodeExists / inputsForNode', () => {
  it('finds a node by id', () => {
    expect(findNode(NODES, '6')).toBe(NODES[1])
    expect(findNode(NODES, '99')).toBeUndefined()
    expect(findNode(NODES, null)).toBeUndefined()
  })

  it('checks node existence', () => {
    expect(nodeExists('3', NODES)).toBe(true)
    expect(nodeExists('99', NODES)).toBe(false)
  })

  it('lists a node inputs', () => {
    expect(inputsForNode(NODES, '3')).toHaveLength(2)
    expect(inputsForNode(NODES, '99')).toEqual([])
  })
})

describe('refExistsInNodes', () => {
  it('treats null/undefined ref as existing (nothing to check)', () => {
    expect(refExistsInNodes(null, NODES)).toBe(true)
    expect(refExistsInNodes(undefined, NODES)).toBe(true)
  })

  it('finds an existing (node, input) pair', () => {
    expect(refExistsInNodes({ node: '6', input: 'text' }, NODES)).toBe(true)
  })

  it('rejects an unknown node or input', () => {
    expect(refExistsInNodes({ node: '99', input: 'text' }, NODES)).toBe(false)
    expect(refExistsInNodes({ node: '6', input: 'missing' }, NODES)).toBe(false)
  })
})

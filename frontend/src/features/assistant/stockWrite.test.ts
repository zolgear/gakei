import { describe, expect, it } from 'vitest'
import { QueryClient } from '@tanstack/react-query'
import { msg } from '../../i18n'
import { makeMessage } from './testFixtures'
import { describeStockUndo, invalidateStockQueries, isStockWriteTool, readStockWrite } from './stockWrite'
import { toolCardKind } from './timeline'

function tool(name: string, result: Record<string, unknown>, ok = true) {
  return makeMessage({ role: 'tool', tool_name: name, ok, result, undoable: true })
}

describe('readStockWrite', () => {
  it('タグの書き込みを、タグ・枚数・サムネイルにする', () => {
    const message = tool('add_tags', {
      operation: 'add_tags',
      tags: ['sky', 'sea'],
      count: 20,
      changed: 15,
      preview: [
        { asset_id: 'a1', focal_point: { x: 0.2, y: 0.3 }, content_rating: 'r' },
        { asset_id: 'a2', focal_point: null, content_rating: null },
        { broken: true },
      ],
    })
    expect(readStockWrite(message)).toEqual({
      tool: 'add_tags',
      count: 20,
      changed: 15,
      tags: ['sky', 'sea'],
      group: null,
      before: null,
      after: null,
      preview: [
        { assetId: 'a1', focalPoint: { x: 0.2, y: 0.3 }, contentRating: 'r' },
        { assetId: 'a2', focalPoint: null, contentRating: null },
      ],
    })
    expect(toolCardKind(message)).toBe('stock')
  })

  it('グループの名前の変更は変更前と変更後を持つ。タイトルの空は空文字', () => {
    const rename = readStockWrite(
      tool('rename_group', { group: { id: 'g1', name: 'After' }, before: { name: 'Before' }, after: { name: 'After' } }),
    )
    expect(rename?.group).toEqual({ id: 'g1', name: 'After' })
    expect([rename?.before, rename?.after]).toEqual(['Before', 'After'])
    const title = readStockWrite(tool('set_title', { count: 1, before: { title: 'old' }, after: { title: null } }))
    expect([title?.before, title?.after]).toEqual(['old', ''])
  })

  it('失敗や別の道具はカードにしない', () => {
    expect(readStockWrite(tool('add_tags', { error: 'x' }, false))).toBeNull()
    expect(toolCardKind(tool('add_tags', { error: 'x' }, false))).toBe('row')
    expect(readStockWrite(tool('suggest_groups', { suggestions: [] }))).toBeNull()
    expect(toolCardKind(tool('suggest_groups', { suggestions: [] }))).toBe('row')
    expect(isStockWriteTool('move_to_group')).toBe(true)
    expect(isStockWriteTool(undefined)).toBe(false)
  })
})

describe('describeStockUndo', () => {
  const t = msg()
  it('戻した枚数と飛ばした枚数、作ったグループの扱いを並べる', () => {
    const text = describeStockUndo(t, 'create_group', {
      restored: 3,
      skipped: 1,
      group_kept: false,
      group_deleted: true,
    })
    expect(text).toContain('3')
    expect(text).toContain('1')
    expect(text).toContain(t.assistant.stockCard.groupDeleted)
  })

  it('名前とタイトルは枚数を出さない', () => {
    expect(describeStockUndo(t, 'rename_group', { restored: 1, skipped: 0, group_kept: false, group_deleted: false })).toBe(
      t.assistant.stockCard.undoneSingle,
    )
  })
})

describe('invalidateStockQueries', () => {
  it('ストック・グループ・画像の詳細・タグを作り直す', () => {
    const client = new QueryClient()
    const keys: unknown[] = []
    client.invalidateQueries = ((filters: { queryKey: unknown }) => {
      keys.push(filters.queryKey)
      return Promise.resolve()
    }) as QueryClient['invalidateQueries']
    invalidateStockQueries(client)
    expect(keys).toEqual(
      expect.arrayContaining([['assets'], ['asset-groups'], ['asset'], ['tags']]),
    )
  })
})

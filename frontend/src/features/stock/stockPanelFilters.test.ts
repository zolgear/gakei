import { describe, expect, it } from 'vitest'
import { isFileDragForUpload, isStockFilterActive } from './stockPanelFilters'

describe('isStockFilterActive', () => {
  it('種類が「すべて」でタグが無ければ絞り込んでいない', () => {
    expect(isStockFilterActive('all', null)).toBe(false)
  })

  it('種類かタグのどちらかで絞っていれば絞り込み中', () => {
    expect(isStockFilterActive('upload', null)).toBe(true)
    expect(isStockFilterActive('all', 'cat')).toBe(true)
  })
})

describe('isFileDragForUpload', () => {
  const app = ['application/x-gakei-asset-id', 'application/x-gakei-group-id']

  it('ファイルを運んでいれば真', () => {
    expect(isFileDragForUpload(['Files'], app)).toBe(true)
  })

  it('アプリ内のタイルやグループのドラッグは偽(File が添えられていても)', () => {
    expect(isFileDragForUpload(['application/x-gakei-asset-id', 'text/uri-list', 'Files'], app)).toBe(false)
    expect(isFileDragForUpload(['application/x-gakei-group-id'], app)).toBe(false)
  })

  it('ファイルの無いドラッグ(文字・リンク)は偽', () => {
    expect(isFileDragForUpload(['text/plain', 'text/uri-list'], app)).toBe(false)
  })
})

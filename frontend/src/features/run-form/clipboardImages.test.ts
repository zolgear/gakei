import { describe, expect, it } from 'vitest'
import { pickPastedImageFiles } from './clipboardImages'

const item = (kind: string, type: string, file: string | null) => ({
  kind,
  type,
  getAsFile: () => file,
})

describe('pickPastedImageFiles', () => {
  it('kind=file かつ image/* の項目だけを返す', () => {
    const files = pickPastedImageFiles([
      item('string', 'text/plain', null),
      item('file', 'image/png', 'shot.png'),
      item('file', 'text/html', 'page.html'),
      item('file', 'image/jpeg', 'photo.jpg'),
    ])
    expect(files).toEqual(['shot.png', 'photo.jpg'])
  })

  it('テキストだけの貼り付けは空配列(通常の貼り付けに任せる)', () => {
    expect(pickPastedImageFiles([item('string', 'text/plain', null)])).toEqual([])
  })

  it('getAsFile が null を返す項目は無視する', () => {
    expect(pickPastedImageFiles([item('file', 'image/png', null)])).toEqual([])
  })
})

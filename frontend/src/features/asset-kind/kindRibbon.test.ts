import { describe, expect, it } from 'vitest'
import { createdLabel, kindRibbon } from './kindRibbon'

describe('kindRibbon', () => {
  it('自分で生成した画像には何も付けない', () => {
    expect(kindRibbon('generated')).toBeNull()
    expect(kindRibbon('generated', false)).toBeNull()
  })

  it('取り込んだ Run の生成画像は「取り込み」', () => {
    expect(kindRibbon('generated', true)).toEqual({ tone: 'imported', label: 'imported' })
  })

  it('アップロードは「アップロード」(取り込みで入ったものも種別のとおり)', () => {
    expect(kindRibbon('upload')).toEqual({ tone: 'upload', label: 'upload' })
    expect(kindRibbon('upload', true)).toEqual({ tone: 'upload', label: 'upload' })
  })

  it('手で作った画像は1つの色にまとめ、文言は種別ごと', () => {
    for (const kind of ['sketch', 'composite', 'mask', 'regions']) {
      expect(kindRibbon(kind)).toEqual({ tone: 'handmade', label: kind })
    }
  })

  it('知らない種別には付けない', () => {
    expect(kindRibbon('something-new')).toBeNull()
  })
})

describe('createdLabel', () => {
  it('種別ごとに日時の行の見出しを言い分ける', () => {
    expect(createdLabel('generated')).toBe('generated')
    expect(createdLabel('generated', true)).toBe('imported')
    expect(createdLabel('upload')).toBe('upload')
    expect(createdLabel('sketch')).toBe('created')
    expect(createdLabel('composite')).toBe('created')
  })
})

import { describe, expect, it } from 'vitest'
import { splitHighlightSegments } from './searchHighlight'

describe('splitHighlightSegments', () => {
  it('一致箇所を1つ強調する', () => {
    expect(splitHighlightSegments('a cat on a chair', 'cat')).toEqual([
      { text: 'a ', highlighted: false },
      { text: 'cat', highlighted: true },
      { text: ' on a chair', highlighted: false },
    ])
  })

  it('大文字小文字を区別しない', () => {
    expect(splitHighlightSegments('A Cat on a chair', 'cat')).toEqual([
      { text: 'A ', highlighted: false },
      { text: 'Cat', highlighted: true },
      { text: ' on a chair', highlighted: false },
    ])
  })

  it('複数語(空白区切り)をそれぞれ強調する', () => {
    expect(splitHighlightSegments('a cat and a dog', 'cat dog')).toEqual([
      { text: 'a ', highlighted: false },
      { text: 'cat', highlighted: true },
      { text: ' and a ', highlighted: false },
      { text: 'dog', highlighted: true },
    ])
  })

  it('繰り返し出現する語をすべて強調する', () => {
    expect(splitHighlightSegments('cat cat', 'cat')).toEqual([
      { text: 'cat', highlighted: true },
      { text: ' ', highlighted: false },
      { text: 'cat', highlighted: true },
    ])
  })

  it('重なる一致範囲はまとめる', () => {
    // "cat" と "at" が重なる -> 1つの強調範囲になる
    expect(splitHighlightSegments('a cat', 'cat at')).toEqual([
      { text: 'a ', highlighted: false },
      { text: 'cat', highlighted: true },
    ])
  })

  it('query が空なら全体を非強調で返す', () => {
    expect(splitHighlightSegments('hello world', '')).toEqual([{ text: 'hello world', highlighted: false }])
    expect(splitHighlightSegments('hello world', '   ')).toEqual([
      { text: 'hello world', highlighted: false },
    ])
  })

  it('一致箇所が無ければ全体を非強調で返す', () => {
    expect(splitHighlightSegments('hello world', 'xyz')).toEqual([
      { text: 'hello world', highlighted: false },
    ])
  })

  it('text が空文字なら空セグメントを返す', () => {
    expect(splitHighlightSegments('', 'cat')).toEqual([{ text: '', highlighted: false }])
  })

  it('一致が文字列の先頭・末尾にある場合も正しく分割する', () => {
    expect(splitHighlightSegments('catfish', 'cat')).toEqual([
      { text: 'cat', highlighted: true },
      { text: 'fish', highlighted: false },
    ])
    expect(splitHighlightSegments('fish cat', 'cat')).toEqual([
      { text: 'fish ', highlighted: false },
      { text: 'cat', highlighted: true },
    ])
  })
})

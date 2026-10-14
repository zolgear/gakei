import { describe, expect, it } from 'vitest'
import { SseParser } from './sseParser'

describe('SseParser', () => {
  it('event と data の組を空行で区切って取り出す', () => {
    const parser = new SseParser()
    const out = parser.push('event: text_delta\ndata: {"type":"text_delta","text":"a"}\n\nevent: done\ndata: {}\n\n')
    expect(out).toEqual([
      { event: 'text_delta', data: '{"type":"text_delta","text":"a"}' },
      { event: 'done', data: '{}' },
    ])
  })

  it('断片の境目が行の途中でもつなぐ', () => {
    const parser = new SseParser()
    expect(parser.push('event: text_')).toEqual([])
    expect(parser.push('delta\nda')).toEqual([])
    expect(parser.push('ta: {"x":1}\n')).toEqual([])
    expect(parser.push('\n')).toEqual([{ event: 'text_delta', data: '{"x":1}' }])
  })

  it('CRLF と、断片の境目で分かれた CR LF を1つの改行として扱う', () => {
    const parser = new SseParser()
    expect(parser.push('data: a\r')).toEqual([])
    expect(parser.push('\n\r\n')).toEqual([{ event: 'message', data: 'a' }])
  })

  it('複数行の data は改行でつなぎ、コメントと知らない項目は読み飛ばす', () => {
    const parser = new SseParser()
    const out = parser.push(': ping\nid: 1\nretry: 10\ndata: one\ndata:two\n\n')
    expect(out).toEqual([{ event: 'message', data: 'one\ntwo' }])
  })

  it('data の無いイベントは出さない', () => {
    const parser = new SseParser()
    expect(parser.push('event: x\n\n')).toEqual([])
  })

  it('end で、最後の空行が無いイベントも出す', () => {
    const parser = new SseParser()
    expect(parser.push('event: done\ndata: {}')).toEqual([])
    expect(parser.end()).toEqual([{ event: 'done', data: '{}' }])
  })
})

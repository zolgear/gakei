/**
 * `text/event-stream` の本文を、届いた断片から1イベントずつ取り出す純粋なパーサー(ADR-0048 8章)。
 * 発言の送信は POST なので `EventSource` が使えず、`fetch` の ReadableStream を自分で読む。
 *
 * - イベントは空行で区切る。行末は `\n` / `\r\n` / `\r` のどれでもよい。
 * - `event:` は種類、`data:` は本文(複数行ならつなぐ)。`:` で始まる行(コメント)と、知らない
 *   項目(`id:`、`retry:` など)は読み飛ばす。値の先頭の空白は1つだけ取る(仕様どおり)。
 * - 断片の境目が行や文字の途中でもよい(文字の復号は呼び出し側の TextDecoder が受け持つ)。
 */

export interface SseMessage {
  /** `event:` の値。無ければ `message`。 */
  event: string
  data: string
}

export class SseParser {
  private buffer = ''
  private eventName = ''
  private dataLines: string[] = []
  private hasData = false

  /** 断片を足し、そこまでで完結したイベントを返す。 */
  push(chunk: string): SseMessage[] {
    this.buffer += chunk
    const messages: SseMessage[] = []
    for (;;) {
      const match = /\r\n|\r|\n/.exec(this.buffer)
      if (!match) break
      // 末尾が `\r` だけのときは、次の断片の `\n` と組かもしれないので待つ。
      if (match[0] === '\r' && match.index === this.buffer.length - 1) break
      const line = this.buffer.slice(0, match.index)
      this.buffer = this.buffer.slice(match.index + match[0].length)
      const message = this.processLine(line)
      if (message) messages.push(message)
    }
    return messages
  }

  /** 本文の終わり。最後の空行が無くても、溜まっているイベントを出す。 */
  end(): SseMessage[] {
    const messages: SseMessage[] = []
    if (this.buffer.length > 0) {
      const line = this.buffer.replace(/\r$/, '')
      this.buffer = ''
      const message = this.processLine(line)
      if (message) messages.push(message)
    }
    const last = this.dispatch()
    if (last) messages.push(last)
    return messages
  }

  private processLine(line: string): SseMessage | null {
    if (line === '') return this.dispatch()
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') {
      this.eventName = value
    } else if (field === 'data') {
      this.dataLines.push(value)
      this.hasData = true
    }
    return null
  }

  private dispatch(): SseMessage | null {
    const message = this.hasData ? { event: this.eventName || 'message', data: this.dataLines.join('\n') } : null
    this.eventName = ''
    this.dataLines = []
    this.hasData = false
    return message
  }
}

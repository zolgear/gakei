import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { msg } from '../i18n'
import { AddToInputsDialog } from '../features/run-form/AddToInputsDialog'
import { ConfirmDialog } from './ConfirmDialog'
import {
  INITIAL_FOCUS_ATTRIBUTE,
  dialogInitialFocusTarget,
  focusOrigin,
  handleModalKeyDown,
  isTopmostDialog,
  nextFocusTarget,
} from './modalFocus'

// 画面のテストの仕組み(jsdom)は入れていないので、`Modal.test.ts` と同じく、描いた HTML の属性と
// 規則(`modalFocus.ts`)を偽の要素で確かめる(ADR-0009 9章)。

class FakeElement {
  readonly name: string
  readonly attrs: Record<string, string>
  isConnected = true
  focusCount = 0

  constructor(name: string, attrs: Record<string, string> = {}) {
    this.name = name
    this.attrs = attrs
  }

  focus() {
    this.focusCount += 1
  }

  getClientRects() {
    return [{}]
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null
  }

  hasAttribute(name: string): boolean {
    return name in this.attrs
  }
}

/** パネルの偽物。`querySelector` は最初のフォーカスの目印が付いた要素を返す。 */
class FakePanel extends FakeElement {
  readonly items: FakeElement[]

  constructor(items: FakeElement[]) {
    super('panel')
    this.items = items
  }

  querySelectorAll(): FakeElement[] {
    return this.items
  }

  querySelector(selector: string): FakeElement | null {
    if (selector !== `[${INITIAL_FOCUS_ATTRIBUTE}]`) return null
    return this.items.find((item) => item.hasAttribute(INITIAL_FOCUS_ATTRIBUTE)) ?? null
  }
}

function asEl(element: FakeElement): HTMLElement {
  return element as unknown as HTMLElement
}

function renderConfirm(extra: Record<string, unknown> = {}): string {
  const props = { open: true, message: '削除しますか?', onConfirm: () => {}, onCancel: () => {}, ...extra }
  return renderToStaticMarkup(createElement(ConfirmDialog, props))
}

describe('ConfirmDialog markup', () => {
  it('パネルに alertdialog と aria-modal を付け、確認文を名前として読ませる', () => {
    const html = renderConfirm()
    // 外側の覆い(overlay)ではなく、パネルの側がダイアログ。
    expect(html).toMatch(/^<div class="[^"]*"><div [^>]*role="alertdialog"/)
    expect(html).toContain('aria-modal="true"')
    expect(html).toContain('tabindex="-1"')
    const labelledBy = /aria-labelledby="([^"]+)"/.exec(html)?.[1]
    expect(labelledBy).toBeTruthy()
    expect(html).toContain(`<p id="${labelledBy}"`)
  })

  it('最初のフォーカスの目印は「キャンセル」にだけ付く', () => {
    const html = renderConfirm()
    const buttons = html.match(/<button[^>]*>[^<]*<\/button>/g) ?? []
    const cancel = buttons.find((b) => b.includes(msg().common.cancel))
    const confirm = buttons.find((b) => b.includes(msg().common.delete))
    expect(cancel).toContain(INITIAL_FOCUS_ATTRIBUTE)
    expect(confirm).not.toContain(INITIAL_FOCUS_ATTRIBUTE)
  })

  it('警告と補足を説明として読ませる', () => {
    const html = renderConfirm({ warning: '注意', previewImageUrl: '/x.png', previewDetail: '512×512' })
    const describedBy = /aria-describedby="([^"]+)"/.exec(html)?.[1] ?? ''
    const ids = describedBy.split(' ')
    expect(ids).toHaveLength(2)
    for (const id of ids) expect(html).toContain(`id="${id}"`)
  })

  it('open が false なら何も描かない', () => {
    expect(renderConfirm({ open: false })).toBe('')
  })
})

describe('AddToInputsDialog markup', () => {
  it('パネルに dialog と aria-modal を付け、確認文を名前として読ませる', () => {
    const props = { open: true, onAdd: () => {}, onReplace: () => {}, onCancel: () => {} }
    const html = renderToStaticMarkup(createElement(AddToInputsDialog, props))
    expect(html).toMatch(/^<div class="[^"]*"><div [^>]*role="dialog"/)
    const labelledBy = /aria-labelledby="([^"]+)"/.exec(html)?.[1]
    expect(html).toContain(`<p id="${labelledBy}"`)
  })
})

describe('dialogInitialFocusTarget', () => {
  it('目印の付いた要素(確認の「キャンセル」)を、先頭の実行ボタンより優先する', () => {
    const danger = new FakeElement('delete')
    const cancel = new FakeElement('cancel', { [INITIAL_FOCUS_ATTRIBUTE]: '' })
    const panel = new FakePanel([danger, cancel])
    expect(dialogInitialFocusTarget(asEl(panel))).toBe(asEl(cancel))
  })

  it('目印の付いた要素が操作できなければ、先頭の操作できる要素にする', () => {
    const first = new FakeElement('first')
    const marked = new FakeElement('marked', { [INITIAL_FOCUS_ATTRIBUTE]: '', disabled: '' })
    const panel = new FakePanel([first, marked])
    expect(dialogInitialFocusTarget(asEl(panel))).toBe(asEl(first))
  })

  it('目印が無ければ先頭の操作できる要素にする', () => {
    const first = new FakeElement('first')
    const panel = new FakePanel([first, new FakeElement('second')])
    expect(dialogInitialFocusTarget(asEl(panel))).toBe(asEl(first))
  })
})

/**
 * 重なりの判定に使う偽の節。`parent` で入れ子を、`order` で文書順を表す
 * (文書順は木を前順に辿った番号。子は親より後ろ)。
 */
class FakeNode {
  readonly name: string
  readonly order: number
  readonly parent: FakeNode | null

  constructor(name: string, order: number, parent: FakeNode | null = null) {
    this.name = name
    this.order = order
    this.parent = parent
  }

  contains(other: FakeNode): boolean {
    for (let node: FakeNode | null = other; node; node = node.parent) if (node === this) return true
    return false
  }

  compareDocumentPosition(other: FakeNode): number {
    // FOLLOWING(4)/ PRECEDING(2)。入れ子の CONTAINS(8)/ CONTAINED_BY(16)も付ける。
    let bits = other.order > this.order ? 4 : 2
    if (this.contains(other)) bits |= 16
    if (other.contains(this)) bits |= 8
    return bits
  }
}

describe('isTopmostDialog', () => {
  it('Modal の中に描いた確認は、Modal より上', () => {
    const modal = new FakeNode('modal', 1)
    const confirm = new FakeNode('confirm', 5, modal)
    expect(isTopmostDialog(confirm, [confirm], [modal])).toBe(true)
  })

  it('ドロワーの中に描いた Modal があれば、ドロワーはキーを扱わない', () => {
    const drawer = new FakeNode('drawer', 1)
    const modal = new FakeNode('modal', 5, drawer)
    expect(isTopmostDialog(drawer, [drawer], [modal])).toBe(false)
  })

  it('同じ仕組みで開いたものは、後から開いたものだけが上', () => {
    const editor = new FakeNode('editor', 1)
    const confirm = new FakeNode('confirm', 9)
    expect(isTopmostDialog(confirm, [editor, confirm], [])).toBe(true)
    expect(isTopmostDialog(editor, [editor, confirm], [])).toBe(false)
  })

  it('文書順で前にある別の Modal は下とみなす', () => {
    const modal = new FakeNode('modal', 1)
    const dialog = new FakeNode('dialog', 5)
    expect(isTopmostDialog(dialog, [dialog], [modal])).toBe(true)
  })

  it('開いていない(一覧に無い)パネルは上ではない', () => {
    const dialog = new FakeNode('dialog', 1)
    expect(isTopmostDialog(dialog, [], [])).toBe(false)
  })
})

describe('入れ子のダイアログの Tab と Esc', () => {
  // Modal(外)の中に確認(内)を開いた状態。内側だけで回し、外側の要素へは出さない。
  const outerInput = new FakeElement('outer-input')
  const danger = new FakeElement('delete')
  const cancel = new FakeElement('cancel', { [INITIAL_FOCUS_ATTRIBUTE]: '' })
  const inner = new FakePanel([danger, cancel])

  it('内側の最後(キャンセル)で Tab なら内側の最初へ回す', () => {
    expect(nextFocusTarget(asEl(inner), asEl(cancel), false)).toBe(asEl(danger))
  })

  it('外側の要素にフォーカスが残っていても、内側へ引き戻す', () => {
    expect(nextFocusTarget(asEl(inner), asEl(outerInput), false)).toBe(asEl(danger))
    expect(nextFocusTarget(asEl(inner), asEl(outerInput), true)).toBe(asEl(cancel))
  })

  it('Esc は内側の閉じる処理だけを呼ぶ', () => {
    const closeInner = vi.fn()
    const event = { key: 'Escape', shiftKey: false, preventDefault: vi.fn() }
    handleModalKeyDown(event, asEl(inner), asEl(cancel), closeInner)
    expect(closeInner).toHaveBeenCalledTimes(1)
  })
})

describe('focusOrigin', () => {
  const body = new FakeNode('body', 0)
  const trigger = new FakeNode('trigger', 1)
  const panel = new FakeNode('panel', 2)
  const inner = new FakeNode('input', 3, panel)

  it('開く前の要素(パネルの外)をそのまま戻す先にする', () => {
    expect(focusOrigin(panel, trigger, null, body)).toBe(trigger)
  })

  it('中の autoFocus が先に動いていたら、その直前の要素を戻す先にする', () => {
    expect(focusOrigin(panel, inner, trigger, body)).toBe(trigger)
  })

  it('body や不明な要素は戻す先にしない', () => {
    expect(focusOrigin(panel, body, null, body)).toBeNull()
    expect(focusOrigin(panel, inner, null, body)).toBeNull()
    expect(focusOrigin(panel, null, null, body)).toBeNull()
  })
})

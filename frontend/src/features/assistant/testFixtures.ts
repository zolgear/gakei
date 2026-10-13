/** テスト用のメッセージを作る(テストからだけ使う)。 */
import type { AssistantMessageResponse } from '../../api/client'

let seq = 0

export function makeMessage(partial: Partial<AssistantMessageResponse> & Pick<AssistantMessageResponse, 'role'>): AssistantMessageResponse {
  seq += 1
  return {
    id: `m${seq}`,
    conversation_id: 'c1',
    position: seq,
    created_at: '2026-10-13T00:00:00Z',
    text: '',
    undoable: false,
    ...partial,
  }
}

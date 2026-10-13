/**
 * 開いていた会話の id を localStorage に覚える(パネルを開き直したとき・リロード後に続きから)。
 * 使えない環境でも壊れないよう try/catch で囲む。
 */
const STORAGE_KEY = 'gakei:assistant-conversation'

export function loadAssistantConversationId(): string | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value && value.length > 0 ? value : null
  } catch {
    return null
  }
}

export function saveAssistantConversationId(conversationId: string | null): void {
  try {
    if (conversationId === null) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, conversationId)
  } catch {
    // 保存できなくても致命的ではない。
  }
}

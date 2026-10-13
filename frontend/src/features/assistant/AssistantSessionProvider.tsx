/**
 * アシスタントの会話の状態をパネルの外(AppShell)に持つ(ADR-0048 2章)。パネルを閉じたり
 * 別のパネルに切り替えたりしても、処理中の応答を切らない(切ると止めたことになる)。
 * 狭い幅のドロワーと広い幅のパネルの2つが同時に描かれても、同じ状態を見る。
 *
 * - 会話の読み込み: 選んだ会話のメッセージを `GET /api/assistant/conversations/{id}` で読む。
 *   見つからなければ(消された・他人の会話)「新しい会話」に戻す。
 * - 送信: 会話が無ければ先に作る。今のフォームの状態と画面の言語を添えて送り、応答の SSE を
 *   reducer に積む。プロンプトセットへの書き込みの結果が届いたら、プロンプトセットの一覧を
 *   取り直す。
 * - 止める: AbortController で接続を切る。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  createAssistantConversation,
  getAssistantConversation,
  openAssistantMessageStream,
  type AssistantMessageResponse,
  type AssistantStreamEvent,
} from '../../api/client'
import { useRunFormContext } from '../../context/useRunFormContext'
import { getLocale, msg } from '../../i18n'
import { assistantFormState } from './assistantForm'
import { assistantSessionReducer, initialAssistantSession } from './assistantSession'
import { readAssistantStream } from './assistantStream'
import { loadAssistantConversationId, saveAssistantConversationId } from './assistantStorage'
import { ASSISTANT_CONVERSATIONS_QUERY_KEY, PROMPT_SETS_QUERY_KEY } from './queryKeys'
import { WRITE_TOOLS } from './timeline'
import { AssistantSessionContext, type AssistantSessionValue } from './useAssistantSession'

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function AssistantSessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { formState } = useRunFormContext()
  // 送信のときに最新の値を読むための ref(描いた後に更新する)。
  const formStateRef = useRef(formState)
  const [state, dispatch] = useReducer(assistantSessionReducer, undefined, () =>
    initialAssistantSession(loadAssistantConversationId()),
  )
  const stateRef = useRef(state)
  useLayoutEffect(() => {
    formStateRef.current = formState
    stateRef.current = state
  })
  const abortRef = useRef<AbortController | null>(null)
  // 選び直した数。読み込みの応答が古い選択のものなら捨てる。
  const [reloadKey, bumpReload] = useReducer((n: number) => n + 1, 0)

  const conversationId = state.conversationId
  useEffect(() => {
    if (conversationId === null) return
    let canceled = false
    getAssistantConversation(conversationId)
      .then((detail) => {
        if (!canceled) dispatch({ type: 'load', conversationId, messages: detail.messages ?? [] })
      })
      .catch((error: unknown) => {
        if (canceled) return
        if (error instanceof ApiError && error.status === 404) {
          saveAssistantConversationId(null)
          dispatch({ type: 'select', conversationId: null })
        }
      })
    return () => {
      canceled = true
    }
  }, [conversationId, reloadKey])

  const selectConversation = useCallback((next: string | null) => {
    if (stateRef.current.sending) return
    saveAssistantConversationId(next)
    dispatch({ type: 'select', conversationId: next })
    bumpReload()
  }, [])

  const handleEvent = useCallback(
    (event: AssistantStreamEvent) => {
      dispatch({ type: 'event', event })
      if (event.type === 'tool_result' && event.message.ok && WRITE_TOOLS.has(event.message.tool_name ?? '')) {
        void queryClient.invalidateQueries({ queryKey: PROMPT_SETS_QUERY_KEY })
      }
      if (event.type === 'run_created') {
        void queryClient.invalidateQueries({ queryKey: ['runs'] })
      }
      if (event.type === 'done') {
        void queryClient.invalidateQueries({ queryKey: ASSISTANT_CONVERSATIONS_QUERY_KEY })
      }
    },
    [queryClient],
  )

  const send = useCallback(
    async (text: string, attachments: string[]): Promise<boolean> => {
      if (stateRef.current.sending) return false
      const m = msg().assistant
      dispatch({ type: 'clearErrors' })
      let id = stateRef.current.conversationId
      if (id === null) {
        try {
          const created = await createAssistantConversation()
          id = created.id
          saveAssistantConversationId(id)
          dispatch({ type: 'select', conversationId: id })
          void queryClient.invalidateQueries({ queryKey: ASSISTANT_CONVERSATIONS_QUERY_KEY })
        } catch (error) {
          dispatch({ type: 'sendFailed', message: error instanceof ApiError ? error.message : m.sendFailed })
          return false
        }
      }
      const controller = new AbortController()
      abortRef.current = controller
      dispatch({ type: 'sendStart', conversationId: id })
      let response: Response
      try {
        response = await openAssistantMessageStream(
          id,
          {
            text,
            attachments,
            form: assistantFormState(formStateRef.current),
            locale: getLocale(),
          },
          controller.signal,
        )
      } catch (error) {
        abortRef.current = null
        if (isAbortError(error)) {
          dispatch({ type: 'stopped', at: new Date().toISOString() })
        } else {
          dispatch({ type: 'sendFailed', message: error instanceof ApiError ? error.message : m.sendFailed })
        }
        return false
      }

      void (async () => {
        let sawDone = false
        try {
          await readAssistantStream(response, (event) => {
            if (event.type === 'done') sawDone = true
            handleEvent(event)
          })
          if (!sawDone) dispatch({ type: 'sendFailed', message: m.connectionLost })
        } catch (error) {
          if (isAbortError(error) || controller.signal.aborted) {
            dispatch({ type: 'stopped', at: new Date().toISOString() })
          } else {
            dispatch({ type: 'sendFailed', message: m.connectionLost })
          }
        } finally {
          if (abortRef.current === controller) abortRef.current = null
          void queryClient.invalidateQueries({ queryKey: ASSISTANT_CONVERSATIONS_QUERY_KEY })
        }
      })()
      return true
    },
    [handleEvent, queryClient],
  )

  const stop = useCallback(() => {
    const controller = abortRef.current
    if (!controller) return
    controller.abort()
    // 読みかけの `read()` が中断で終わるのを待たずに、手元でも止めた状態にする。
    dispatch({ type: 'stopped', at: new Date().toISOString() })
  }, [])

  // プロバイダーが外れるとき(ログアウトなど)は止める。
  useEffect(() => () => abortRef.current?.abort(), [])

  const replaceMessage = useCallback((message: AssistantMessageResponse) => {
    dispatch({ type: 'replaceMessage', message })
  }, [])
  const clearErrors = useCallback(() => dispatch({ type: 'clearErrors' }), [])

  const value = useMemo<AssistantSessionValue>(
    () => ({ state, selectConversation, send, stop, replaceMessage, clearErrors }),
    [state, selectConversation, send, stop, replaceMessage, clearErrors],
  )
  return <AssistantSessionContext.Provider value={value}>{children}</AssistantSessionContext.Provider>
}

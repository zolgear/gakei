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
 * - 生成フォームの書き込み(`update_form`。ADR-0048 11章): 応答を受けている間に届いた成功の
 *   結果だけをフォームに反映する(`requestFormLoad`)。反映の直前の値を記録し、カードの
 *   「元に戻す」に使う。会話の読み込みでは反映しない。記録はこのページを開いている間だけ持つ。
 * - 画像の許可(ADR-0048 14.1): カードの答えを `POST /api/assistant/permissions/{request_id}` で
 *   送る。「常に許可」はユーザーの設定も変わるので、設定を取り直す。
 * - 止める: AbortController で接続を切る。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  answerAssistantPermission,
  createAssistantConversation,
  getAssistantConversation,
  openAssistantMessageStream,
  type AssistantMessageResponse,
  type AssistantPermissionAnswer,
  type AssistantStreamEvent,
} from '../../api/client'
import { USER_PREFERENCES_QUERY_KEY } from '../settings/userPreferences'
import { useRunFormContext } from '../../context/useRunFormContext'
import { getLocale, msg } from '../../i18n'
import { assistantFormState } from './assistantForm'
import { getStudioResultAssetId } from '../workspace/studioResultStore'
import { getStockSelectedAssetIds } from '../stock/stockSelectionStore'
import { assistantSessionReducer, initialAssistantSession } from './assistantSession'
import { applyFormWrite, formWriteFromStreamEvent, type FormWriteRecord } from './formWrite'
import { readAssistantStream } from './assistantStream'
import { loadAssistantConversationId, saveAssistantConversationId } from './assistantStorage'
import { ASSISTANT_CONVERSATIONS_QUERY_KEY, PROMPT_SETS_QUERY_KEY } from './queryKeys'
import { WRITE_TOOLS } from './timeline'
import { invalidateStockQueries, isStockWriteTool } from './stockWrite'
import { AssistantSessionContext, type AssistantSessionValue } from './useAssistantSession'

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

export function AssistantSessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { formState, setFormState, requestFormLoad } = useRunFormContext()
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
  const [formWrites, setFormWrites] = useState<Record<string, FormWriteRecord>>({})
  const formWritesRef = useRef(formWrites)
  useLayoutEffect(() => {
    formWritesRef.current = formWrites
  })
  // 反映した書き込みのメッセージ(同じイベントが2度来ても重ねて反映しない)。
  const appliedFormWritesRef = useRef(new Set<string>())

  // フォームの全体の値を入れる。context(スタジオの外・入力画像)と、マウント済みのスタジオの
  // ローカルの状態(読み込みのリクエスト)の両方に書く(Run 詳細の「同じ設定で新規作成」と同じ)。
  const loadForm = useCallback(
    (next: typeof formState) => {
      formStateRef.current = next
      setFormState(next)
      requestFormLoad({ kind: 'assistant', state: next })
    },
    [setFormState, requestFormLoad],
  )
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
      const formWrite = formWriteFromStreamEvent(event)
      if (formWrite && event.type === 'tool_result' && !appliedFormWritesRef.current.has(event.message.id)) {
        const id = event.message.id
        appliedFormWritesRef.current.add(id)
        const before = formStateRef.current
        const after = applyFormWrite(before, formWrite)
        loadForm(after)
        setFormWrites((prev) => ({ ...prev, [id]: { before, after, undone: false } }))
      }
      if (event.type === 'tool_result' && event.message.ok && WRITE_TOOLS.has(event.message.tool_name ?? '')) {
        void queryClient.invalidateQueries({ queryKey: PROMPT_SETS_QUERY_KEY })
      }
      // ストックの整理(ADR-0048 13章)の後は、ストックとグループ、画像のタグ・タイトルを取り直す。
      if (event.type === 'tool_result' && event.message.ok && isStockWriteTool(event.message.tool_name)) {
        invalidateStockQueries(queryClient)
      }
      if (event.type === 'run_created') {
        void queryClient.invalidateQueries({ queryKey: ['runs'] })
      }
      if (event.type === 'done') {
        void queryClient.invalidateQueries({ queryKey: ASSISTANT_CONVERSATIONS_QUERY_KEY })
      }
    },
    [queryClient, loadForm],
  )

  const undoFormWrite = useCallback(
    (messageId: string) => {
      const record = formWritesRef.current[messageId]
      if (!record || record.undone) return
      loadForm(record.before)
      setFormWrites((prev) => ({ ...prev, [messageId]: { ...record, undone: true } }))
    },
    [loadForm],
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
            form: assistantFormState(formStateRef.current, getStudioResultAssetId(), getStockSelectedAssetIds()),
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

  const answerPermission = useCallback(
    async (requestId: string, answer: AssistantPermissionAnswer): Promise<void> => {
      dispatch({ type: 'permissionAnswer', requestId, answer })
      try {
        await answerAssistantPermission(requestId, answer)
      } catch (error) {
        // 届かなかったら、もう一度選べるように戻す。
        dispatch({ type: 'permissionAnswer', requestId, answer: null })
        throw error
      }
      if (answer === 'always') void queryClient.invalidateQueries({ queryKey: USER_PREFERENCES_QUERY_KEY })
    },
    [queryClient],
  )

  const value = useMemo<AssistantSessionValue>(
    () => ({
      state,
      selectConversation,
      send,
      stop,
      replaceMessage,
      clearErrors,
      formWrites,
      undoFormWrite,
      answerPermission,
    }),
    [state, selectConversation, send, stop, replaceMessage, clearErrors, formWrites, undoFormWrite, answerPermission],
  )
  return <AssistantSessionContext.Provider value={value}>{children}</AssistantSessionContext.Provider>
}

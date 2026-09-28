/**
 * フォームの状態をページ間で渡すための軽い仕組み(状態管理ライブラリは使わない)。
 * 「同じ設定で再実行」(Run詳細)と「これを元に Edit」(ビューア)は、
 * ここへ値をセットしてからスタジオへ遷移するだけ。実行は必ずユーザー操作を経る。
 *
 * 画面移動での保持はこの Context(メモリ)が担うが、リロードや iOS Safari のタブ復帰
 * (バックグラウンドで破棄される)をまたいだ保持のため、setFormState の1箇所で
 * localStorage への保存も行う(呼び出し元ごとに保存を意識させない)。プロンプトの
 * 入力中に連打にならないよう、保存自体は 300ms デバウンスする(画面への反映は即時)。
 *
 * グループ(ADR-0022)は、変わったときにだけ「最後に選んだグループ」として別のキーにも
 * すぐ保存する(`lastAssetGroupStorage`)。フォームでの選択・「+」での作成・「同じ設定で」の
 * 復元はどれもここを通るので、呼び出し元ごとに保存を意識させない。「新規生成」やフォームの
 * 初期化では、この値を既定のグループにする。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ensureInputIds } from '../features/run-form/editInputs'
import { createEmptyFormState, type RunFormState } from '../features/run-form/types'
import { loadLastAssetGroupId, saveLastAssetGroupId } from './lastAssetGroupStorage'
import { loadRunFormState, saveRunFormState } from './runFormStorage'
import { RunFormContext } from './useRunFormContext'

const SAVE_DEBOUNCE_MS = 300

function initialFormState(): RunFormState {
  // 保存されたフォームが無ければ、グループだけは最後に選んだものを既定にする。
  return loadRunFormState() ?? { ...createEmptyFormState(), assetGroupId: loadLastAssetGroupId() }
}

export function RunFormProvider({ children }: { children: ReactNode }) {
  const [formState, setFormStateRaw] = useState<RunFormState>(initialFormState)
  const formStateRef = useRef(formState)
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [pendingPromptInsert, setPendingPromptInsert] = useState<{ text: string; nonce: number } | null>(
    null,
  )
  const insertNonceRef = useRef(0)

  function flushSave() {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current)
      saveTimeoutRef.current = null
    }
    saveRunFormState(formStateRef.current)
  }

  const setFormState = useCallback((nextRaw: RunFormState) => {
    // 入力の inputId が欠けた・重複した状態を保存しないための保険(issue #12・#13)。
    // 揃っていれば同じオブジェクトのまま通す。
    const inputs = ensureInputIds(nextRaw.inputs)
    const next = inputs === nextRaw.inputs ? nextRaw : { ...nextRaw, inputs }
    if (next.assetGroupId !== formStateRef.current.assetGroupId) saveLastAssetGroupId(next.assetGroupId)
    formStateRef.current = next
    setFormStateRaw(next)
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    saveTimeoutRef.current = setTimeout(() => {
      saveTimeoutRef.current = null
      saveRunFormState(next)
    }, SAVE_DEBOUNCE_MS)
  }, [])

  const requestPromptInsert = useCallback((text: string) => {
    insertNonceRef.current += 1
    setPendingPromptInsert({ text, nonce: insertNonceRef.current })
  }, [])

  const clearPendingPromptInsert = useCallback(() => setPendingPromptInsert(null), [])

  useEffect(() => {
    // iOS Safari はバックグラウンドに回ったタブを破棄することがあるため、デバウンス中の
    // 保存をそのタイミングで前倒しして確定させる(取りこぼしを減らす保険)。
    function handleVisibilityChange() {
      if (document.visibilityState === 'hidden') flushSave()
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('pagehide', flushSave)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.removeEventListener('pagehide', flushSave)
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const value = useMemo(
    () => ({
      formState,
      setFormState,
      pendingPromptInsert,
      requestPromptInsert,
      clearPendingPromptInsert,
    }),
    [formState, setFormState, pendingPromptInsert, requestPromptInsert, clearPendingPromptInsert],
  )
  return <RunFormContext.Provider value={value}>{children}</RunFormContext.Provider>
}

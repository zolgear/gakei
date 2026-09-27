/**
 * 小さなインライン通知(トースト)。ブラウザのダイアログ(alert等)は使わない。
 * 5秒で自動的に消える。「元に戻す」等のアクションを1つだけ置ける。
 * `useToast()` で状態を持ち、`<ToastHost>` を各ページの末尾に置いて表示する。
 */
import { useEffect, useReducer, useRef } from 'react'
import { useI18n } from '../i18n'
import { initialToastState, toastReducer, type ActiveToast, type ToastPayload } from './toastReducer'
import styles from './Toast.module.css'

const TOAST_DURATION_MS = 5000

export interface UseToastResult {
  toast: ActiveToast | null
  show: (payload: ToastPayload) => void
  dismiss: () => void
}

export function useToast(): UseToastResult {
  const [state, dispatch] = useReducer(toastReducer, initialToastState)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    if (!state.toast) return
    const id = state.toast.id
    timeoutRef.current = setTimeout(() => dispatch({ type: 'expire', id }), TOAST_DURATION_MS)
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [state.toast])

  return {
    toast: state.toast,
    show: (payload: ToastPayload) => dispatch({ type: 'show', payload }),
    dismiss: () => dispatch({ type: 'dismiss' }),
  }
}

interface ToastHostProps {
  toast: ActiveToast | null
  onDismiss: () => void
}

export function ToastHost({ toast, onDismiss }: ToastHostProps) {
  const { t } = useI18n()
  if (!toast) return null

  return (
    <div className={styles.host}>
      <div className={styles.toast} role="status">
        <span className={styles.message}>{toast.message}</span>
        {toast.actionLabel && toast.onAction && (
          <button
            type="button"
            className={styles.actionButton}
            onClick={() => {
              toast.onAction?.()
              onDismiss()
            }}
          >
            {toast.actionLabel}
          </button>
        )}
        <button type="button" className={styles.closeButton} aria-label={t.common.close} onClick={onDismiss}>
          ×
        </button>
      </div>
    </div>
  )
}

/**
 * トースト(小さなインライン通知)の表示/消去の状態遷移。純粋関数のみ(副作用なし)。
 * 自動で消えるタイマー(setTimeout)は呼び出し側(`useToast` フック)が持つが、
 * 「そのタイマーが指していたトーストが、既に次のトーストに差し替わっていたら消さない」
 * という判定だけはここに寄せてテストできるようにする(id で比較する)。
 */
export interface ToastPayload {
  message: string
  actionLabel?: string
  onAction?: () => void
}

export interface ActiveToast extends ToastPayload {
  id: number
}

export interface ToastState {
  toast: ActiveToast | null
  nextId: number
}

export type ToastEvent =
  | { type: 'show'; payload: ToastPayload }
  | { type: 'dismiss' }
  | { type: 'expire'; id: number }

export const initialToastState: ToastState = { toast: null, nextId: 1 }

export function toastReducer(state: ToastState, event: ToastEvent): ToastState {
  switch (event.type) {
    case 'show':
      return { toast: { id: state.nextId, ...event.payload }, nextId: state.nextId + 1 }
    case 'dismiss':
      return state.toast === null ? state : { ...state, toast: null }
    case 'expire':
      // 表示中のトーストの id と一致するときだけ消す(古いタイマーが、後から出た
      // 新しいトーストを誤って消さないようにする)。
      if (state.toast !== null && state.toast.id === event.id) {
        return { ...state, toast: null }
      }
      return state
    default:
      return state
  }
}

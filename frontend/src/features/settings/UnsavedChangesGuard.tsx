/**
 * 保存していない変更があるとき、ページを離れる前に確かめる(ADR-0031 4章)。
 * - アプリの中の移動(目次、「戻る」、App バーなど)とブラウザの戻る: React Router の `useBlocker`
 *   (データルーターでしか動かない。`main.tsx` の `createBrowserRouter`)。
 * - タブを閉じる・再読み込み: `beforeunload`(文言はブラウザが決める)。
 * 同じパスの中の移動(ハッシュや state だけの変化)は止めない。
 */
import { useEffect } from 'react'
import { useBlocker } from 'react-router'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useI18n } from '../../i18n'

export function UnsavedChangesGuard({ dirty }: { dirty: boolean }) {
  const { t } = useI18n()
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname,
  )

  useEffect(() => {
    if (!dirty) return
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault()
      // 古いブラウザは returnValue が要る。
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [dirty])

  // 確認を出している間に変更が無くなった(保存に成功した等)ら、そのまま移動させる。
  useEffect(() => {
    if (blocker.state === 'blocked' && !dirty) blocker.proceed()
  }, [blocker, dirty])

  return (
    <ConfirmDialog
      open={blocker.state === 'blocked'}
      message={t.settings.leave.message}
      confirmLabel={t.settings.leave.discard}
      cancelLabel={t.settings.leave.stay}
      onConfirm={() => blocker.proceed?.()}
      onCancel={() => blocker.reset?.()}
    />
  )
}

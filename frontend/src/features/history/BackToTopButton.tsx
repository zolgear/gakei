/**
 * 履歴の右下に浮かぶ「上へ戻る」。スクロールの容器(`scrollRef`)が1画面分以上下へ進んだら出し、
 * 押すと先頭へ戻す。画像の邪魔をしないよう、小さく半透明にする。
 */
import { useEffect, useState, type RefObject } from 'react'
import { ArrowUpIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import { scrollElementToTop, shouldShowBackToTop } from './backToTop'
import styles from './BackToTopButton.module.css'

export function BackToTopButton({ scrollRef }: { scrollRef: RefObject<HTMLElement | null> }) {
  const { t } = useI18n()
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => setVisible(shouldShowBackToTop(el.scrollTop, el.clientHeight))
    update()
    el.addEventListener('scroll', update, { passive: true })
    return () => el.removeEventListener('scroll', update)
  }, [scrollRef])

  if (!visible) return null
  const label = t.history.page.backToTop
  return (
    <button
      type="button"
      className={styles.button}
      aria-label={label}
      title={label}
      onClick={() => scrollElementToTop(scrollRef.current)}
    >
      <ArrowUpIcon size={18} />
    </button>
  )
}

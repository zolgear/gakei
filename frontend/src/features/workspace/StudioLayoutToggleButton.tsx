/**
 * 生成画面の入力欄の配置(下段/サイドバー)を切り替えるボタン(ADR-0009 1章・2026-09-26 追記)。
 * `ResultPane` のヘッダー右上に置く。設定画面の同じ設定と同期する(`useStudioLayout`)。
 */
import { useI18n } from '../../i18n'
import { nextStudioLayout, setStudioLayout } from './studioLayout'
import { useStudioLayout } from './useStudioLayout'

export function StudioLayoutToggleButton({ className }: { className?: string }) {
  const { t } = useI18n()
  const layout = useStudioLayout()
  const label =
    layout === 'bottom'
      ? t.workspace.studioWorkspace.layoutToggle.toSidebar
      : t.workspace.studioWorkspace.layoutToggle.toBottom

  return (
    <button
      type="button"
      className={className}
      data-layout={layout}
      aria-label={label}
      title={label}
      onClick={() => setStudioLayout(nextStudioLayout(layout))}
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
        <rect
          x="1.5"
          y="2.5"
          width="11"
          height="9"
          rx="1"
          stroke="currentColor"
          strokeWidth="1.5"
        />
        {layout === 'bottom' ? (
          <path d="M5.5 2.5V11.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        ) : (
          <path d="M1.5 9.5H12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        )}
      </svg>
    </button>
  )
}

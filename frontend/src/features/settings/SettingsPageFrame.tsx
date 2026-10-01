/**
 * 設定の各ページの枠(ADR-0031 3章)。上部に「戻る」、ページ名、変更の件数、「変更を取り消す」、
 * 「保存」を1行に並べ、スクロールしても上に残す(スマホも上)。
 * 「保存で反映」の項目があるページだけ `draft` を渡す。渡さないページ(即時・操作だけのページ)には
 * 保存のボタンを出さない。
 * 保存していない変更がある間は、目次に点を付け(`reportDirty`)、離れる前に確かめる。
 */
import { useEffect, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { fmt, useI18n } from '../../i18n'
import { isOpenedFromToc, settingsBackDestination, type SettingsPageId } from './settingsPages'
import { useSettingsShell } from './settingsShell'
import type { SettingsDraftControls } from './useSettingsDraft'
import { UnsavedChangesGuard } from './UnsavedChangesGuard'
import styles from './settings.module.css'

interface SettingsPageFrameProps {
  pageId: SettingsPageId
  title: string
  intro?: ReactNode
  draft?: SettingsDraftControls
  children: ReactNode
}

export function SettingsPageFrame({ pageId, title, intro, draft, children }: SettingsPageFrameProps) {
  const { t } = useI18n()
  const f = t.settings.frame
  const { isWide, reportDirty } = useSettingsShell()
  const location = useLocation()
  const navigate = useNavigate()
  const dirty = draft?.dirty ?? false

  useEffect(() => {
    reportDirty(pageId, dirty)
  }, [pageId, dirty, reportDirty])
  useEffect(() => () => reportDirty(pageId, false), [pageId, reportDirty])

  function goBack() {
    const dest = settingsBackDestination({
      isWide,
      openedFromToc: isOpenedFromToc(location.state),
      locationKey: location.key,
    })
    if (dest.type === 'back') navigate(-1)
    else navigate(dest.path, { replace: true })
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.headerRow}>
          <button type="button" className={styles.backButton} onClick={goBack}>
            {t.common.back}
          </button>
          <h1 className={styles.pageTitle}>{title}</h1>
          {draft && (
            <div className={styles.headerActions}>
              {draft.changedCount > 0 && (
                <span className={styles.changeCount} role="status">
                  {fmt(f.changeCount, { count: draft.changedCount })}
                </span>
              )}
              <button
                type="button"
                className={styles.secondaryButton}
                disabled={!draft.dirty || draft.saving}
                onClick={draft.reset}
              >
                {f.discard}
              </button>
              <button type="button" className={styles.primaryButton} disabled={!draft.canSave} onClick={draft.save}>
                {draft.saving ? f.saving : f.save}
              </button>
            </div>
          )}
        </div>
        {draft?.saveError && (
          <p className={styles.saveError} role="alert">
            {draft.saveError}
          </p>
        )}
      </header>

      {intro && <p className={styles.intro}>{intro}</p>}

      {children}

      <UnsavedChangesGuard dirty={dirty} />
    </div>
  )
}

/**
 * `/settings/display`(ADR-0031 1章)。表示言語、タブのアイコンで進捗を示すか、生成画面の入力欄の配置
 * (ADR-0009 1章・2026-09-26 追記)。どれも「即時」の項目(ブラウザに保存し、その場で反映する)
 * なので、このページには保存のボタンを出さない。
 */
import { useSyncExternalStore } from 'react'
import { LANGUAGE_SETTING_LABEL, LOCALES, LOCALE_LABELS, isLocale, useI18n } from '../../../i18n'
import {
  getFaviconProgressEnabled,
  setFaviconProgressEnabled,
  subscribeFaviconProgressEnabled,
} from '../../favicon/faviconPrefs'
import { isStudioLayout, setStudioLayout } from '../../workspace/studioLayout'
import { useStudioLayout } from '../../workspace/useStudioLayout'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import styles from '../settings.module.css'

export function DisplaySettingsPage() {
  const { t, locale, setLocale } = useI18n()
  const d = t.settings.display
  const faviconProgressEnabled = useSyncExternalStore(
    subscribeFaviconProgressEnabled,
    getFaviconProgressEnabled,
    getFaviconProgressEnabled,
  )
  const studioLayout = useStudioLayout()

  return (
    <SettingsPageFrame pageId="display" title={t.settings.pages.display} intro={d.intro}>
      <SettingsSection>
        <SettingsRow label={LANGUAGE_SETTING_LABEL} htmlFor="gakei-locale">
          <select
            id="gakei-locale"
            className={styles.select}
            value={locale}
            onChange={(e) => {
              if (isLocale(e.target.value)) setLocale(e.target.value)
            }}
          >
            {LOCALES.map((l) => (
              <option key={l} value={l}>
                {LOCALE_LABELS[l]}
              </option>
            ))}
          </select>
        </SettingsRow>

        <SettingsRow label={d.faviconProgress.label} htmlFor="gakei-favicon-progress" description={d.faviconProgress.help}>
          <SettingsSwitch
            id="gakei-favicon-progress"
            checked={faviconProgressEnabled}
            onChange={setFaviconProgressEnabled}
          />
        </SettingsRow>

        <SettingsRow label={d.studioLayout.label} htmlFor="gakei-studio-layout" description={d.studioLayout.help}>
          <select
            id="gakei-studio-layout"
            className={styles.select}
            value={studioLayout}
            onChange={(e) => {
              if (isStudioLayout(e.target.value)) setStudioLayout(e.target.value)
            }}
          >
            <option value="bottom">{d.studioLayout.optionBottom}</option>
            <option value="sidebar">{d.studioLayout.optionSidebar}</option>
          </select>
        </SettingsRow>
      </SettingsSection>
    </SettingsPageFrame>
  )
}

/**
 * `/settings/display`(ADR-0031 1章)。表示言語、タブのアイコンで進捗を示すか、生成画面の入力欄の配置
 * (ADR-0009 1章・2026-09-26 追記)、スケッチとマスクをストックに出すか(ADR-0035)、プロンプトのタグ補完と
 * タグの日本語訳の表示(ADR-0041 3章・4章)。どれも「即時」の項目(ブラウザに保存し、その場で反映する)
 * なので、このページには保存のボタンを出さない。
 *
 * 末尾の「ブラウザに保存した設定」(2026-10-04)は、GAKEI がこのブラウザに置いたキー(`lib/browserStorage.ts`)
 * をまとめて消し、メモリ上の状態も戻すためにページを読み込み直す。即時の操作(確認ダイアログを経る)。
 */
import { useState, useSyncExternalStore } from 'react'
import { ConfirmDialog } from '../../../components/ConfirmDialog'
import { clearGakeiBrowserStorageAndReload } from '../../../lib/browserStorage'
import { LANGUAGE_SETTING_LABEL, LOCALES, LOCALE_LABELS, isLocale, useI18n } from '../../../i18n'
import {
  getFaviconProgressEnabled,
  setFaviconProgressEnabled,
  subscribeFaviconProgressEnabled,
} from '../../favicon/faviconPrefs'
import { setStockShowSketchMask, useStockShowSketchMask } from '../../stock/stockPrefs'
import { isStudioLayout, setStudioLayout } from '../../workspace/studioLayout'
import { useStudioLayout } from '../../workspace/useStudioLayout'
import {
  isTagCompletionMode,
  setShowTagTranslations,
  setTagCompletionMode,
  useShowTagTranslationsPref,
  useTagCompletionMode,
} from '../../tag-dictionary/tagCompletionPrefs'
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
  const stockShowSketchMask = useStockShowSketchMask()
  const tagCompletionMode = useTagCompletionMode()
  const showTagTranslations = useShowTagTranslationsPref()
  const [confirmClearOpen, setConfirmClearOpen] = useState(false)
  const b = d.browserStorage

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

        <SettingsRow
          label={d.stockShowSketchMask.label}
          htmlFor="gakei-stock-show-sketch-mask"
          description={d.stockShowSketchMask.help}
        >
          <SettingsSwitch
            id="gakei-stock-show-sketch-mask"
            checked={stockShowSketchMask}
            onChange={setStockShowSketchMask}
          />
        </SettingsRow>

        <SettingsRow label={d.tagCompletion.label} htmlFor="gakei-tag-completion" description={d.tagCompletion.help}>
          <select
            id="gakei-tag-completion"
            className={styles.select}
            value={tagCompletionMode}
            onChange={(e) => {
              if (isTagCompletionMode(e.target.value)) setTagCompletionMode(e.target.value)
            }}
          >
            <option value="tag-providers">{d.tagCompletion.optionTagProviders}</option>
            <option value="always">{d.tagCompletion.optionAlways}</option>
            <option value="off">{d.tagCompletion.optionOff}</option>
          </select>
        </SettingsRow>

        <SettingsRow
          label={d.tagTranslations.label}
          htmlFor="gakei-tag-translations"
          description={d.tagTranslations.help}
        >
          <SettingsSwitch
            id="gakei-tag-translations"
            checked={showTagTranslations}
            onChange={setShowTagTranslations}
          />
        </SettingsRow>
      </SettingsSection>

      <SettingsSection heading={b.heading}>
        <SettingsRow label={b.label} description={b.help}>
          <button type="button" className={styles.dangerButton} onClick={() => setConfirmClearOpen(true)}>
            {b.clear}
          </button>
        </SettingsRow>
      </SettingsSection>

      <ConfirmDialog
        open={confirmClearOpen}
        message={b.confirmMessage}
        warning={b.confirmWarning}
        confirmLabel={b.confirmLabel}
        onConfirm={() => {
          // 各機能はモジュールや React の状態に読み込み済みの値を持つので、読み込み直して既定に戻す。
          clearGakeiBrowserStorageAndReload()
        }}
        onCancel={() => setConfirmClearOpen(false)}
      />
    </SettingsPageFrame>
  )
}

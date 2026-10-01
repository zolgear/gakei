/**
 * 「操作」だけのページ(ADR-0031 2章: ボタンを押すとその場で実行する。取り消せないものは確認を出す)
 * と、「GAKEI について」。中身はこれまでのセクションをそのまま使い、共通の枠に入れる。
 * 保存の対象が無いので、ヘッダーに保存のボタンは出さない。
 */
import { useI18n } from '../../../i18n'
import { AboutSection } from '../AboutSection'
import { ApiTokensSection } from '../ApiTokensSection'
import { ProfileSection } from '../ProfileSection'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { SharesSection } from '../SharesSection'
import { useSettingsShell } from '../settingsShell'

export function ProfileSettingsPage() {
  const { t } = useI18n()
  const { toast } = useSettingsShell()
  return (
    <SettingsPageFrame pageId="profile" title={t.settings.pages.profile}>
      <ProfileSection toast={toast} />
    </SettingsPageFrame>
  )
}

export function SharesSettingsPage() {
  const { t } = useI18n()
  const { toast } = useSettingsShell()
  return (
    <SettingsPageFrame pageId="shares" title={t.settings.pages.shares} intro={t.settings.shares.intro}>
      <SharesSection toast={toast} />
    </SettingsPageFrame>
  )
}

export function AccessTokensSettingsPage() {
  const { t } = useI18n()
  const { toast } = useSettingsShell()
  return (
    <SettingsPageFrame pageId="accessTokens" title={t.settings.pages.accessTokens} intro={t.settings.accessTokens.intro}>
      <ApiTokensSection toast={toast} />
    </SettingsPageFrame>
  )
}

export function AboutSettingsPage() {
  const { t } = useI18n()
  return (
    <SettingsPageFrame pageId="about" title={t.settings.pages.about}>
      <AboutSection />
    </SettingsPageFrame>
  )
}

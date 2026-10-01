/**
 * `/settings/share-links`(管理者。ADR-0029 5章、ADR-0031)。共有リンク機能の有効/無効(既定は無効)。
 * スイッチも「保存で反映」の項目なので、ヘッダーの「保存」を押すまで送らない。
 * 個人モードで外に出すときの逆プロキシの注意は、画面では変えられない運用上の情報なので
 * ここには書かず、`docs/sharing.md` にだけ書く。
 */
import { useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getShareSettings, updateShareSettings } from '../../../api/client'
import { useI18n } from '../../../i18n'
import { SHARE_SETTINGS_QUERY_KEY } from '../queryKeys'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection, SettingsSwitch } from '../SettingsParts'
import { useSettingsDraft } from '../useSettingsDraft'

interface ShareLinksDraft {
  enabled: boolean
}

export function ShareLinksSettingsPage() {
  const { t } = useI18n()
  const s = t.settings.shareAdmin
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: SHARE_SETTINGS_QUERY_KEY, queryFn: getShareSettings })
  const saved = useMemo<ShareLinksDraft | undefined>(
    () => (query.data ? { enabled: query.data.enabled } : undefined),
    [query.data],
  )
  const draft = useSettingsDraft<ShareLinksDraft>({
    saved,
    save: async (patch) => {
      if (patch.enabled === undefined) return
      const next = await updateShareSettings(patch.enabled)
      queryClient.setQueryData(SHARE_SETTINGS_QUERY_KEY, next)
    },
  })

  return (
    <SettingsPageFrame pageId="shareLinks" title={t.settings.pages.shareLinks} intro={s.intro} draft={draft}>
      <QueryStatus
        isLoading={query.isLoading}
        isError={query.isError}
        loadingText={s.loading}
        errorText={s.loadFailed}
        retryText={s.retry}
        onRetry={() => void query.refetch()}
      />
      {draft.values && (
        <SettingsSection>
          <SettingsRow
            label={s.enabledLabel}
            htmlFor="gakei-share-enabled"
            description={s.enabledTooltip}
            changed={draft.isChanged('enabled')}
          >
            <SettingsSwitch
              id="gakei-share-enabled"
              checked={draft.values.enabled}
              disabled={draft.saving}
              onChange={(checked) => draft.set('enabled', checked)}
            />
          </SettingsRow>
        </SettingsSection>
      )}
    </SettingsPageFrame>
  )
}

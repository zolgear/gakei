/**
 * `/settings/mods`(管理者。ADR-0051 5・6・7章、ADR-0031)。
 *
 * - 注意書き: Python の Mod は GAKEI と同じ権限で動くこと、外部へ実行者の情報や画像を送ること。
 *   詳しくは docs(`docs/mods.md`)に任せ、ここは短く。
 * - 「保存で反映」: イベントに載せるこの GAKEI の名前(`instance.name`)。ヘッダーの「保存」で送る。
 * - 「接続」: Webhook(追加・編集・テスト送信・削除)と Python の Mod(有効化・設定・テスト送信)。
 *   ダイアログでその場で送り、ページの保存には混ぜない。
 * - 「操作」: 呼ぶ順番(上下のボタンでその場で送る)。
 * - 表示だけ: 直近の呼び出しと、配達の失敗。
 */
import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getMods, updateMods } from '../../../api/client'
import { fmt, useI18n } from '../../../i18n'
import { ModActivitySection } from '../mods/ModActivitySection'
import { ModOrderSection } from '../mods/ModOrderSection'
import { PythonModsSection } from '../mods/PythonModsSection'
import { WebhooksSection } from '../mods/WebhooksSection'
import { MOD_INSTANCE_NAME_MAX, isValidInstanceName } from '../modsSettings'
import { MODS_QUERY_KEY } from '../queryKeys'
import type { DraftErrors } from '../settingsDraft'
import { SettingsPageFrame } from '../SettingsPageFrame'
import { QueryStatus, SettingsRow, SettingsSection } from '../SettingsParts'
import { useSettingsDraft } from '../useSettingsDraft'
import styles from '../settings.module.css'
import own from '../mods/mods.module.css'

interface ModsDraft {
  instance_name: string
}

export function ModsSettingsPage() {
  const { t } = useI18n()
  const m = t.settings.mods
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: MODS_QUERY_KEY, queryFn: getMods })
  const data = query.data
  const instanceName = data?.instance_name

  const saved = useMemo<ModsDraft | undefined>(
    () => (data ? { instance_name: instanceName ?? '' } : undefined),
    [data, instanceName],
  )
  const validate = useCallback(
    (v: ModsDraft): DraftErrors<ModsDraft> =>
      isValidInstanceName(v.instance_name)
        ? {}
        : { instance_name: fmt(m.instanceNameInvalid, { max: MOD_INSTANCE_NAME_MAX }) },
    [m.instanceNameInvalid],
  )
  const draft = useSettingsDraft<ModsDraft>({
    saved,
    validate,
    save: async (patch) => {
      if (patch.instance_name === undefined) return
      const name = patch.instance_name.trim()
      const next = await updateMods({ instance_name: name || null })
      queryClient.setQueryData(MODS_QUERY_KEY, next)
    },
  })
  const values = draft.values

  return (
    <SettingsPageFrame pageId="mods" title={t.settings.pages.mods} intro={m.intro} draft={draft}>
      <div className={styles.warningBox} role="note">
        <p className={`${styles.warningText} ${own.notice}`}>{m.noticeExternal}</p>
        <p className={`${styles.warningText} ${own.notice}`}>{m.noticePython}</p>
      </div>
      <QueryStatus
        isLoading={query.isLoading}
        isError={query.isError}
        loadingText={m.loading}
        errorText={m.loadFailed}
        retryText={m.retry}
        onRetry={() => void query.refetch()}
      />
      {data && values && (
        <>
          <WebhooksSection webhooks={data.webhooks} />
          <PythonModsSection mods={data.python_mods} />
          <ModOrderSection data={data} />

          <SettingsSection heading={m.instanceHeading}>
            <SettingsRow
              label={m.instanceNameLabel}
              htmlFor="gakei-mods-instance-name"
              description={m.instanceNameHelp}
              changed={draft.isChanged('instance_name')}
            >
              <input
                id="gakei-mods-instance-name"
                type="text"
                className={styles.input}
                value={values.instance_name}
                placeholder={m.instanceNamePlaceholder}
                aria-invalid={draft.errors.instance_name ? true : undefined}
                disabled={draft.saving}
                onChange={(e) => draft.set('instance_name', e.target.value)}
              />
              {draft.errors.instance_name && <p className={styles.errorText}>{draft.errors.instance_name}</p>}
            </SettingsRow>
          </SettingsSection>

          <ModActivitySection data={data} />
        </>
      )}
    </SettingsPageFrame>
  )
}

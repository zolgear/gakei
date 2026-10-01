/**
 * 「保存で反映」の項目(ADR-0031 2章)の下書きを持つフック。
 * 保存済みの値(サーバーから読んだもの)と、利用者が変えた値(上書き)を分けて持ち、
 * 変えたキーの差分・検証・保存(差分だけを1回で送る)・取り消しをまとめて扱う。
 * 保存の成否はここでまとめて示す(成功はトースト「保存しました」、失敗はページのヘッダーの下)。
 * 保存に成功したら上書きを捨てる。保存済みの値は、`save` の中でクエリのキャッシュを
 * 更新してもらう(応答の値が画面に戻る)。
 */
import { useCallback, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { useI18n } from '../../i18n'
import {
  canSaveDraft,
  changedDraftKeys,
  draftPatch,
  draftValues,
  hasDraftErrors,
  setDraftOverride,
  type DraftErrors,
} from './settingsDraft'
import { useSettingsShell } from './settingsShell'

/** ページのヘッダー(`SettingsPageFrame`)が使う部分。 */
export interface SettingsDraftControls {
  changedCount: number
  dirty: boolean
  canSave: boolean
  saving: boolean
  saveError: string | null
  save: () => void
  reset: () => void
}

export interface SettingsDraft<T extends object> extends SettingsDraftControls {
  /** 下書きの値。保存済みの値がまだ無い(読み込み中・失敗)ときは undefined。 */
  values: T | undefined
  set: <K extends keyof T>(key: K, value: T[K]) => void
  isChanged: (key: keyof T) => boolean
  errors: DraftErrors<T>
}

interface UseSettingsDraftOptions<T extends object> {
  saved: T | undefined
  validate?: (values: T) => DraftErrors<T>
  /** 変えたキーだけを渡す。成功したらクエリのキャッシュを更新する。 */
  save: (patch: Partial<T>) => Promise<unknown>
}

export function useSettingsDraft<T extends object>({
  saved,
  validate,
  save,
}: UseSettingsDraftOptions<T>): SettingsDraft<T> {
  const { t } = useI18n()
  const { toast } = useSettingsShell()
  const [overrides, setOverrides] = useState<Partial<T>>({})

  const mutation = useMutation({
    mutationFn: (patch: Partial<T>) => save(patch),
    onSuccess: () => {
      setOverrides({})
      toast.show({ message: t.settings.frame.savedToast })
    },
  })

  const values = useMemo(() => (saved ? draftValues(saved, overrides) : undefined), [saved, overrides])
  const changed = useMemo(() => (saved ? changedDraftKeys(saved, overrides) : []), [saved, overrides])
  const errors = useMemo<DraftErrors<T>>(() => (values && validate ? validate(values) : {}), [values, validate])
  const valid = !hasDraftErrors(errors)

  const resetMutation = mutation.reset
  const set = useCallback(
    <K extends keyof T>(key: K, value: T[K]) => {
      if (!saved) return
      setOverrides((prev) => setDraftOverride(saved, prev, key, value))
      // 値を変えたら、前回の保存の失敗表示は消す。
      resetMutation()
    },
    [saved, resetMutation],
  )

  const saveError = mutation.error
    ? mutation.error instanceof ApiError
      ? mutation.error.message
      : t.settings.frame.saveFailed
    : null

  return {
    values,
    set,
    isChanged: (key) => changed.includes(key),
    errors,
    changedCount: changed.length,
    dirty: changed.length > 0,
    canSave: canSaveDraft({ changedCount: changed.length, valid, saving: mutation.isPending }),
    saving: mutation.isPending,
    saveError,
    save: () => {
      if (!saved || !canSaveDraft({ changedCount: changed.length, valid, saving: mutation.isPending })) return
      mutation.mutate(draftPatch(saved, overrides))
    },
    reset: () => {
      setOverrides({})
      resetMutation()
    },
  }
}

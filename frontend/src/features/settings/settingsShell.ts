/**
 * 設定画面の枠(`pages/SettingsPage.tsx`)が、中の各ページに渡すもの(ADR-0031 5章)。
 * - `toast`: 設定画面で1つだけ持つトースト。
 * - `isWide`: 目次と本文を横に並べているか(設定ページの幅で判定。「戻る」の行き先が変わる)。
 * - `reportDirty`: 保存していない変更の有無を目次に知らせる(目次の項目に点を付ける)。
 */
import { createContext, useContext } from 'react'
import type { UseToastResult } from '../../components/Toast'
import type { SettingsPageId } from './settingsPages'

export interface SettingsShellValue {
  toast: UseToastResult
  isWide: boolean
  reportDirty: (page: SettingsPageId, dirty: boolean) => void
}

export const SettingsShellContext = createContext<SettingsShellValue | null>(null)

export function useSettingsShell(): SettingsShellValue {
  const value = useContext(SettingsShellContext)
  if (!value) throw new Error('useSettingsShell は設定画面の中でだけ使えます')
  return value
}

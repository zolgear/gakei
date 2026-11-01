/**
 * 履歴の並べ替え(新しい順 / 古い順 / グループ / 似た順。ADR-0054 5章・6章)。絞り込みと同じアイコンのボタンと
 * 小さなメニュー。似た順は埋め込みが使えるときだけ出す。日付以外を選んでいる間はボタンに印を付ける。
 */
import { SortIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import type { HistorySort } from './historySort'
import { HistoryToolbarMenu, type HistoryToolbarMenuOption } from './HistoryToolbarMenu'

interface HistorySortMenuProps {
  value: HistorySort
  onChange: (value: HistorySort) => void
  embeddingsAvailable: boolean
}

export function HistorySortMenu({ value, onChange, embeddingsAvailable }: HistorySortMenuProps) {
  const { t } = useI18n()
  const options: HistoryToolbarMenuOption<HistorySort>[] = [
    { id: 'date', label: t.history.page.sortDate },
    { id: 'date_asc', label: t.history.page.sortDateAsc },
    { id: 'group', label: t.history.page.sortGroup },
  ]
  if (embeddingsAvailable) options.push({ id: 'embedding', label: t.history.page.sortEmbedding })
  return (
    <HistoryToolbarMenu
      label={t.history.page.sort}
      icon={<SortIcon size={16} />}
      options={options}
      value={value}
      onChange={onChange}
      active={value !== 'date'}
    />
  )
}

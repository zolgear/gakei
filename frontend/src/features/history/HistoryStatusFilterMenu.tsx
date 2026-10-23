/**
 * 履歴の絞り込み。状態(すべて / 成功 / 失敗)と、それとは独立のお気に入り(ADR-0055)。
 * ふだんは使わないので、ツールバーにはアイコンのボタン1つだけを置き、押すと小さなメニューで選ぶ
 * (`HistoryToolbarMenu`)。どちらかで絞っている間はボタンに印を付ける。
 */
import { FilterIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import type { StatusFilter } from './historyFilters'
import { HistoryToolbarMenu } from './HistoryToolbarMenu'

interface HistoryStatusFilterMenuProps {
  value: StatusFilter
  onChange: (value: StatusFilter) => void
  favoriteOnly: boolean
  onFavoriteOnlyChange: (value: boolean) => void
}

export function HistoryStatusFilterMenu({
  value,
  onChange,
  favoriteOnly,
  onFavoriteOnlyChange,
}: HistoryStatusFilterMenuProps) {
  const { t } = useI18n()
  return (
    <HistoryToolbarMenu
      label={t.history.page.filterByStatus}
      icon={<FilterIcon size={16} />}
      options={[
        { id: 'all', label: t.history.page.filterAll },
        { id: 'succeeded', label: t.history.page.filterSucceeded },
        { id: 'failed', label: t.history.page.filterFailed },
      ]}
      value={value}
      onChange={onChange}
      active={value !== 'all' || favoriteOnly}
      toggles={[
        {
          id: 'favorite',
          label: t.history.page.filterFavorite,
          checked: favoriteOnly,
          onChange: onFavoriteOnlyChange,
        },
      ]}
    />
  )
}

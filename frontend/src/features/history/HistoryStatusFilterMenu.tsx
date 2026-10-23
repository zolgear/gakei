/**
 * 履歴の状態の絞り込み(すべて / 成功 / 失敗)。ふだんは使わないので、ツールバーにはアイコンの
 * ボタン1つだけを置き、押すと小さなメニューで選ぶ(`HistoryToolbarMenu`)。
 */
import { FilterIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import type { StatusFilter } from './historyFilters'
import { HistoryToolbarMenu } from './HistoryToolbarMenu'

interface HistoryStatusFilterMenuProps {
  value: StatusFilter
  onChange: (value: StatusFilter) => void
}

export function HistoryStatusFilterMenu({ value, onChange }: HistoryStatusFilterMenuProps) {
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
      active={value !== 'all'}
    />
  )
}

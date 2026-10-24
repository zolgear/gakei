/**
 * 履歴の絞り込み。状態(すべて / 成功 / 失敗)、出力の形(すべての形 / 正方形 / ポートレート / ワイド)、
 * それとは独立のお気に入り(ADR-0055)・透過・領域(詳細のカードのラベルと対。`historyLabels.ts`)。
 * ふだんは使わないので、ツールバーにはアイコンのボタン1つだけを置き、押すと小さなメニューで選ぶ
 * (`HistoryToolbarMenu`)。どれかで絞っている間はボタンに印を付ける。
 */
import { FilterIcon } from '../../components/icons'
import { useI18n } from '../../i18n'
import type { StatusFilter } from './historyFilters'
import { hasLabelFilter, type HistoryFilter } from './historyFilterUrl'
import { HISTORY_SHAPES, type HistoryShape } from './historyLabels'
import { HistoryToolbarMenu } from './HistoryToolbarMenu'

const ANY_SHAPE = 'any'

interface HistoryStatusFilterMenuProps {
  filter: HistoryFilter
  onChange: (patch: Partial<HistoryFilter>) => void
}

export function HistoryStatusFilterMenu({ filter, onChange }: HistoryStatusFilterMenuProps) {
  const { t } = useI18n()
  const shapeLabels: Record<HistoryShape, string> = {
    square: t.history.labels.square,
    portrait: t.history.labels.portrait,
    wide: t.history.labels.wide,
  }
  return (
    <HistoryToolbarMenu<StatusFilter>
      label={t.history.page.filterByStatus}
      icon={<FilterIcon size={16} />}
      options={[
        { id: 'all', label: t.history.page.filterAll },
        { id: 'succeeded', label: t.history.page.filterSucceeded },
        { id: 'failed', label: t.history.page.filterFailed },
      ]}
      value={filter.status}
      onChange={(status) => onChange({ status })}
      active={filter.status !== 'all' || filter.favoriteOnly || hasLabelFilter(filter)}
      groups={[
        {
          id: 'shape',
          label: t.history.page.filterShape,
          options: [
            { id: ANY_SHAPE, label: t.history.page.filterShapeAll },
            ...HISTORY_SHAPES.map((shape) => ({ id: shape, label: shapeLabels[shape] })),
          ],
          value: filter.shape ?? ANY_SHAPE,
          onChange: (value) => onChange({ shape: HISTORY_SHAPES.find((s) => s === value) ?? null }),
        },
      ]}
      toggles={[
        {
          id: 'favorite',
          label: t.history.page.filterFavorite,
          checked: filter.favoriteOnly,
          onChange: (favoriteOnly) => onChange({ favoriteOnly }),
        },
        {
          id: 'transparent',
          label: t.history.page.filterTransparent,
          checked: filter.transparentOnly,
          onChange: (transparentOnly) => onChange({ transparentOnly }),
        },
        {
          id: 'regions',
          label: t.history.page.filterRegions,
          checked: filter.regionsOnly,
          onChange: (regionsOnly) => onChange({ regionsOnly }),
        },
      ]}
    />
  )
}

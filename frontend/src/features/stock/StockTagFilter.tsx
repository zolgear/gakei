/**
 * ストックの「タグで絞り込む」(ADR-0024 5章)。見出し行の絞り込みのポップオーバー(`StockFilterMenu`)の
 * 中に置く、候補付きの入力欄(`TagAutocomplete`、候補は件数順)。選ぶと今のタグと置き換える。
 * 選んだタグは `stockTagFilterStore` に置き(ビューアのタグのチップからも設定できる)、絞っている間は
 * パネルの見出し行の下にチップ(× で解除)を出す(`StockPanel`)。見出しの文字は置かない。
 */
import { useI18n } from '../../i18n'
import { TagAutocomplete } from '../annotations/TagAutocomplete'
import { normalizeTagName } from '../annotations/tagInput'
import { setStockTagFilter } from './stockTagFilterStore'

export function StockTagFilter() {
  const { t } = useI18n()
  const f = t.stock.tagFilter
  return (
    <TagAutocomplete
      placeholder={f.placeholder}
      ariaLabel={f.placeholder}
      suggestionsLabel={f.suggestions}
      onPick={(name) => setStockTagFilter(normalizeTagName(name) || null)}
    />
  )
}

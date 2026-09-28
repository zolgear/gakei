/**
 * タグ名の入力と候補(ADR-0024 5章)。ビューアの「タグを追加」とストックの「タグで絞り込む」で使う。
 * 候補は `GET /api/tags?q=`(件数順)を 200ms 遅らせて引き、既に付いているタグ(`exclude`)を除く。
 * 候補のリストは入力欄の直下に文書の流れのまま出す(絶対配置のポップアップにしない)。サイドバーや
 * 情報欄は `overflow: auto` のスクロール領域なので、浮かせると下端で切れて隠れるため。
 * ↑/↓ で候補を選び、Enter で決める(候補を選んでいなければ入力した文字のまま)。Esc で候補を閉じる。
 */
import { useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listTags } from '../../api/client'
import { useDebouncedValue } from '../search/useDebouncedValue'
import { filterTagSuggestions, moveSuggestionIndex, normalizeTagName } from './tagInput'
import styles from './TagAutocomplete.module.css'

const SUGGESTION_LIMIT = 8
/** 除外する分を見込んで多めに取る。 */
const FETCH_LIMIT = 20

interface TagAutocompleteProps {
  placeholder: string
  ariaLabel: string
  suggestionsLabel: string
  /**
   * 決めたとき(候補のクリック、Enter、追加ボタン)。入力した文字のままの場合もある。
   * false を返すと入力を消さずに残す(重複などで受け付けなかったとき)。
   */
  onPick: (name: string) => boolean | void
  exclude?: readonly { name: string }[]
  /** 渡すと入力欄の右に送信ボタンを出す(ビューアの「追加」)。 */
  submitLabel?: string
  disabled?: boolean
  /** 入力が変わったとき(呼び出し側のエラー表示を消すなど)。 */
  onInputChange?: (text: string) => void
}

export function TagAutocomplete({
  placeholder,
  ariaLabel,
  suggestionsLabel,
  onPick,
  exclude = [],
  submitLabel,
  disabled = false,
  onInputChange,
}: TagAutocompleteProps) {
  const listId = useId()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [text, setText] = useState('')
  const [focused, setFocused] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const query = useDebouncedValue(normalizeTagName(text), 200)

  const tagsQuery = useQuery({
    queryKey: ['tags', query, FETCH_LIMIT],
    queryFn: () => listTags({ q: query || undefined, limit: FETCH_LIMIT }),
    enabled: focused && !disabled,
    staleTime: 30_000,
  })
  const suggestions = filterTagSuggestions(tagsQuery.data?.items ?? [], exclude, SUGGESTION_LIMIT)
  const open = focused && !dismissed && !disabled && suggestions.length > 0
  const active = open && activeIndex < suggestions.length ? activeIndex : -1

  function updateText(value: string) {
    setText(value)
    setDismissed(false)
    setActiveIndex(-1)
    onInputChange?.(value)
  }

  function pick(name: string) {
    if (onPick(name) === false) return
    setText('')
    setActiveIndex(-1)
    inputRef.current?.focus()
  }

  function submitTyped() {
    if (active >= 0) {
      pick(suggestions[active].name)
      return
    }
    if (normalizeTagName(text)) pick(text)
  }

  return (
    <div className={styles.root}>
      <div className={styles.row}>
        <input
          ref={inputRef}
          type="text"
          className={styles.input}
          value={text}
          placeholder={placeholder}
          aria-label={ariaLabel}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listId}
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          disabled={disabled}
          maxLength={200}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            setFocused(false)
            setActiveIndex(-1)
          }}
          onChange={(e) => updateText(e.target.value)}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
              if (suggestions.length === 0) return
              e.preventDefault()
              setDismissed(false)
              setActiveIndex(moveSuggestionIndex(active, suggestions.length, e.key === 'ArrowDown' ? 1 : -1))
            } else if (e.key === 'Enter') {
              e.preventDefault()
              submitTyped()
            } else if (e.key === 'Escape' && open) {
              e.preventDefault()
              e.stopPropagation()
              setDismissed(true)
              setActiveIndex(-1)
            }
          }}
        />
        {submitLabel && (
          <button
            type="button"
            className={styles.submitButton}
            disabled={disabled || !normalizeTagName(text)}
            // 押しても入力欄のフォーカスを外さない(候補の表示を保つ)。
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              if (normalizeTagName(text)) pick(text)
            }}
          >
            {submitLabel}
          </button>
        )}
      </div>
      {open && (
        <ul id={listId} role="listbox" aria-label={suggestionsLabel} className={styles.list}>
          {suggestions.map((item, index) => (
            <li
              key={item.name}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              className={styles.option}
              data-active={index === active}
              // 入力欄の blur より先に候補を選べるよう、mousedown で既定動作(フォーカス移動)を止める。
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(item.name)}
            >
              <span className={styles.optionName}>{item.name}</span>
              <span className={styles.optionCount}>{item.count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

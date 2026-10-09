/**
 * プロンプトのタグ編集モード(ADR-0039 2章)。プロンプトの文字列をトップレベルのカンマで区切って
 * チップで出し、× で消す。入力欄に打つと候補(`GET /api/tags/suggestions`、200ms 遅らせて引く)を
 * 入力欄のすぐ下に出し、タップか Enter で足す(候補のタグ名は `_` を空白に、括弧は送り先に応じて
 * エスケープする)。カンマを打つと、それまでに打った文字をそのまま1つのタグとして確定する
 * (括弧を開いたままのカンマでは確定しない)。入力欄からフォーカスが外れたときも、打ちかけの
 * 文字を確定する(テキストモードと同じく、打った文字を失わないため)。
 *
 * 編集はすべて `promptTags.ts` の純粋関数で行い、区切りを `, ` に揃える以外は文字列を書き換えない。
 * 重みの調整や並べ替えは作らない(テキストモードで直す)。
 *
 * 候補のリストは文書の流れのまま出す(`TagAutocomplete` と同じ。スクロール領域の下端で切れない)。
 */
import { useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { suggestPromptTags } from '../../api/client'
import { fmt, useI18n } from '../../i18n'
import { useDebouncedValue } from '../search/useDebouncedValue'
import { moveSuggestionIndex } from '../annotations/tagInput'
import {
  addPromptTags,
  promptTagKind,
  removePromptTag,
  splitPromptTags,
  splitTypedTags,
  tagCompareKey,
  tagNameToPrompt,
} from './promptTags'
import styles from './PromptTagEditor.module.css'

const SUGGESTION_LIMIT = 8
/** 既に入っているタグを除く分を見込んで、API の上限まで取る。 */
const FETCH_LIMIT = 20

interface PromptTagEditorProps {
  /** 入力欄の id(`<label htmlFor>` と、挿入位置の読み取り(`id="prompt"`)に使う)。 */
  id: string
  value: string
  onChange: (next: string) => void
  /** 候補を足すときに括弧をエスケープするか(OpenAI のフォームではしない)。 */
  escapeParens: boolean
  /** 欄の名前(「プロンプト」など)。入力欄の読み上げに使う。 */
  fieldLabel: string
  /** 外枠に足すクラス(高さなど、置き場所ごとの調整)。 */
  className?: string
}

export function PromptTagEditor({ id, value, onChange, escapeParens, fieldLabel, className }: PromptTagEditorProps) {
  const { t } = useI18n()
  const pt = t.promptTags
  const listId = useId()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [text, setText] = useState('')
  const [focused, setFocused] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)

  const tags = splitPromptTags(value)
  const typed = text.trim()
  // 括弧などで始まる入力(重み・LoRA・Dynamic Prompts)は候補を引かない。
  const wantsSuggestions = typed.length > 0 && !/^[([{<]/.test(typed)
  const query = useDebouncedValue(wantsSuggestions ? typed : '', 200)

  const suggestionsQuery = useQuery({
    queryKey: ['tags', 'suggestions', query],
    queryFn: () => suggestPromptTags({ q: query, limit: FETCH_LIMIT }),
    enabled: focused && query.length > 0,
    staleTime: 30_000,
  })
  const present = new Set(tags.map(tagCompareKey))
  const suggestions = wantsSuggestions
    ? (suggestionsQuery.data?.items ?? []).filter((item) => !present.has(tagCompareKey(item.name))).slice(0, SUGGESTION_LIMIT)
    : []
  const open = focused && !dismissed && suggestions.length > 0
  const active = open && activeIndex < suggestions.length ? activeIndex : -1

  function resetInput(next = '') {
    setText(next)
    setActiveIndex(-1)
    setDismissed(false)
  }

  function commit(additions: string[]) {
    const next = addPromptTags(value, additions)
    if (next !== value) onChange(next)
  }

  function pickSuggestion(name: string) {
    commit([tagNameToPrompt(name, escapeParens)])
    resetInput()
    inputRef.current?.focus()
  }

  function commitTyped() {
    if (typed) commit([typed])
    resetInput()
  }

  function handleChange(next: string) {
    const { complete, rest } = splitTypedTags(next)
    if (complete.length > 0) {
      commit(complete)
      resetInput(rest)
      return
    }
    resetInput(next)
  }

  function handleKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      // 送信のショートカット。打ちかけがあれば先に確定し(このときは送信しない)、
      // 無ければそのまま親(フォーム)に任せる。
      if (typed) {
        e.preventDefault()
        e.stopPropagation()
        commitTyped()
      }
      return
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (suggestions.length === 0) return
      e.preventDefault()
      setDismissed(false)
      setActiveIndex(moveSuggestionIndex(active, suggestions.length, e.key === 'ArrowDown' ? 1 : -1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (active >= 0) pickSuggestion(suggestions[active].name)
      else commitTyped()
    } else if (e.key === 'Escape' && open) {
      e.preventDefault()
      e.stopPropagation()
      setDismissed(true)
      setActiveIndex(-1)
    }
  }

  return (
    <div
      className={`${styles.box} ${className ?? ''}`}
      onMouseDown={(e) => {
        // 枠の空いたところを押したら入力欄にフォーカスする(チップや候補の操作は除く)。
        if (e.target === e.currentTarget) {
          e.preventDefault()
          inputRef.current?.focus()
        }
      }}
    >
      {tags.length > 0 ? (
        <ul className={styles.chips}>
          {tags.map((tag, index) => (
            <li key={`${index}:${tag}`} className={styles.chip} data-kind={promptTagKind(tag)}>
              <span className={styles.chipLabel} title={tag}>
                {tag}
              </span>
              <button
                type="button"
                className={styles.chipRemove}
                aria-label={fmt(pt.removeTag, { tag })}
                title={fmt(pt.removeTag, { tag })}
                // 入力欄のフォーカスを外さない(打ちかけを確定させずに消す)。
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => onChange(removePromptTag(value, index))}
              >
                <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                  <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.empty}>{pt.empty}</p>
      )}
      <input
        ref={inputRef}
        id={id}
        type="text"
        className={styles.input}
        value={text}
        placeholder={pt.inputPlaceholder}
        aria-label={fmt(pt.inputLabel, { field: fieldLabel })}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        enterKeyHint="enter"
        onFocus={() => setFocused(true)}
        onBlur={() => {
          setFocused(false)
          commitTyped()
        }}
        onChange={(e) => handleChange(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      {open && (
        <ul id={listId} role="listbox" aria-label={pt.suggestions} className={styles.suggestions}>
          {suggestions.map((item, index) => (
            <li
              key={item.name}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              className={styles.option}
              data-active={index === active}
              data-source={item.source}
              // 入力欄の blur(打ちかけの確定)より先に候補を選べるよう、フォーカスの移動を止める。
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pickSuggestion(item.name)}
            >
              <span className={styles.optionName}>{item.name}</span>
              <span className={styles.optionCount}>{item.count.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * テキストモードのプロンプト欄での、タグの入力アシスト(ADR-0041 3章)。キャレットの位置の
 * 「カンマで区切られた入力中の語」(`caretTagQuery.ts`)に、タグの候補(`GET /api/tags/suggestions`)を
 * `@` のポップオーバーと同じ出し方(caret の行のすぐ上か下、`document.body` 直下)で出す。
 * `<lora:` を打っている途中は、SD WebUI の LoRA の一覧から候補を出す(SD WebUI のときだけ)。
 *
 * キー操作は `@` と同じ(↑↓ で選び、Enter / Tab で確定、Esc で閉じる)。Esc で閉じた語は、語が変わるまで
 * 開かない。IME の変換中(compositionstart〜end)は候補を出さず、キーも奪わない。
 * 選ぶと語をタグで置き換え、トップレベルなら `, ` を補う(括弧のエスケープは ADR-0039 と同じ規則)。
 *
 * 呼び出し側は textarea の onChange / onClick / onKeyUp で `refresh`、onKeyDown で `handleKeyDown`
 * (true を返したら処理済み)を呼び、`textareaProps` を textarea に渡し、`popover` を描く。
 * `@` の呼び出しが開いているときは呼び出し側が `close` し、こちらを使わない。
 */
import { useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { listSdWebuiLoras, suggestPromptTags, type SdWebuiLora, type TagSuggestion } from '../../api/client'
import { useI18n } from '../../i18n'
import { isMobileViewport } from '../../lib/viewport'
import { useDebouncedValue } from '../search/useDebouncedValue'
import { useMentionPlacement } from '../prompt-sets/useMentionPlacement'
import popoverStyles from '../prompt-sets/MentionPopover.module.css'
import { tagNameToPrompt } from '../prompt-tags/promptTags'
import { filterLoras, loraPromptTag, LORA_WEIGHT_DEFAULT } from '../sdwebui/loraPrompt'
import { SDWEBUI_LORAS_QUERY_KEY } from '../sdwebui/loraQuery'
import { applyTagCompletion, findTagQueryAtCaret, type TagQueryMatch } from './caretTagQuery'
import { TagSuggestionLabel } from './TagSuggestionLabel'
import { useTagTranslationsEnabled } from './useTagTranslations'
import styles from './TextTagAssist.module.css'

const DEBOUNCE_MS = 150
const SUGGESTION_LIMIT = 20
const LORA_LIMIT = 20

type Candidate =
  | { kind: 'tag'; key: string; insert: string; item: TagSuggestion }
  | { kind: 'lora'; key: string; insert: string; lora: SdWebuiLora }

interface UseTextTagAssistOptions {
  textareaRef: RefObject<HTMLTextAreaElement | null>
  /** textarea の今の値。 */
  value: string
  /** 置き換えた後の値を反映する。 */
  onChange: (next: string) => void
  /** 候補を出すか(ユーザー設定とプロバイダー、欄が使えるか)。 */
  enabled: boolean
  /** `<lora:` の候補を出すか(SD WebUI のときだけ)。 */
  loraEnabled: boolean
  /** 括弧をエスケープするか(ADR-0039。OpenAI ではしない)。 */
  escapeParens: boolean
  /** ポップオーバーの id(`aria-controls` と候補の id の接頭辞)。 */
  popoverId: string
}

export interface TextTagAssist {
  open: boolean
  /** textarea の `aria-activedescendant` に渡す値。 */
  activeDescendant: string | undefined
  refresh: (el: HTMLTextAreaElement) => void
  close: () => void
  handleKeyDown: (e: ReactKeyboardEvent<HTMLTextAreaElement>) => boolean
  textareaProps: {
    onCompositionStart: () => void
    onCompositionEnd: (e: React.CompositionEvent<HTMLTextAreaElement>) => void
    onBlur: () => void
  }
  popover: React.ReactNode
}

function matchKey(match: TagQueryMatch | null): string {
  return match ? `${match.kind}:${match.start}:${match.query}` : ''
}

export function useTextTagAssist({
  textareaRef,
  value,
  onChange,
  enabled,
  loraEnabled,
  escapeParens,
  popoverId,
}: UseTextTagAssistOptions): TextTagAssist {
  const { t } = useI18n()
  const showTranslations = useTagTranslationsEnabled()
  const [match, setMatch] = useState<TagQueryMatch | null>(null)
  const [active, setActive] = useState<{ key: string; index: number }>({ key: '', index: 0 })
  const [dismissedKey, setDismissedKey] = useState<string | null>(null)
  const composingRef = useRef(false)
  const pendingCursorRef = useRef<number | null>(null)

  const currentKey = matchKey(match)
  // 遅らせるのは文字列にする(オブジェクトだと描画のたびに別の値になり、遅延がやり直しになる)。
  const debouncedTagQuery = useDebouncedValue(match?.kind === 'tag' ? match.query : '', DEBOUNCE_MS)
  const tagQuery = match?.kind === 'tag' ? debouncedTagQuery : ''

  const suggestionsQuery = useQuery({
    queryKey: ['tags', 'suggestions', tagQuery],
    queryFn: () => suggestPromptTags({ q: tagQuery, limit: SUGGESTION_LIMIT }),
    enabled: enabled && match?.kind === 'tag' && tagQuery.length > 0,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  })
  const lorasQuery = useQuery({
    queryKey: SDWEBUI_LORAS_QUERY_KEY,
    queryFn: listSdWebuiLoras,
    enabled: enabled && loraEnabled && match?.kind === 'lora',
    staleTime: 60_000,
    retry: false,
  })

  let candidates: Candidate[] = []
  if (match?.kind === 'tag' && tagQuery.length > 0) {
    candidates = (suggestionsQuery.data?.items ?? []).map((item) => ({
      kind: 'tag',
      key: item.name,
      insert: tagNameToPrompt(item.name, escapeParens),
      item,
    }))
  } else if (match?.kind === 'lora') {
    candidates = filterLoras(lorasQuery.data?.items ?? [], match.query)
      .slice(0, LORA_LIMIT)
      .map((lora) => ({ kind: 'lora', key: lora.name, insert: loraPromptTag(lora.name, LORA_WEIGHT_DEFAULT), lora }))
  }

  const open = enabled && match !== null && dismissedKey !== currentKey && candidates.length > 0
  const activeIndex = open && active.key === currentKey && active.index < candidates.length ? active.index : 0
  const placement = useMentionPlacement(textareaRef, open ? (match?.start ?? null) : null, candidates.length)

  // 置き換えた値が描画された後で、キャレットを置き換えた語の後ろへ移す。
  useLayoutEffect(() => {
    const cursor = pendingCursorRef.current
    if (cursor === null) return
    pendingCursorRef.current = null
    const el = textareaRef.current
    if (!el) return
    if (document.activeElement !== el && !isMobileViewport()) el.focus()
    el.setSelectionRange(cursor, cursor)
  }, [value, textareaRef])

  function setMatchIfChanged(next: TagQueryMatch | null) {
    setMatch((prev) => {
      if (prev === next) return prev
      if (prev && next && matchKey(prev) === matchKey(next) && prev.end === next.end && prev.depth === next.depth) {
        return prev
      }
      return next
    })
  }

  function refresh(el: HTMLTextAreaElement) {
    if (composingRef.current) return
    if (!enabled || el.selectionStart !== el.selectionEnd) {
      setMatchIfChanged(null)
      return
    }
    const found = findTagQueryAtCaret(el.value, el.selectionStart)
    if (found?.kind === 'lora' && !loraEnabled) {
      setMatchIfChanged(null)
      return
    }
    setMatchIfChanged(found)
  }

  function close() {
    setMatchIfChanged(null)
  }

  function select(candidate: Candidate) {
    if (!match) return
    const result = applyTagCompletion(value, match, candidate.insert)
    pendingCursorRef.current = result.cursor
    setMatch(null)
    onChange(result.text)
  }

  function handleKeyDown(e: ReactKeyboardEvent<HTMLTextAreaElement>): boolean {
    // IME の変換中の Enter などは奪わない(Safari は変換を確定した直後の keydown が keyCode 229 で来る)。
    if (composingRef.current || e.nativeEvent.isComposing || e.keyCode === 229) return false
    if (!open) return false
    // Ctrl / Cmd + Enter は送信のショートカット(フォームに任せる)。
    if (e.ctrlKey || e.metaKey || e.altKey) return false
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActive({ key: currentKey, index: (activeIndex + 1) % candidates.length })
        return true
      case 'ArrowUp':
        e.preventDefault()
        setActive({ key: currentKey, index: (activeIndex - 1 + candidates.length) % candidates.length })
        return true
      case 'Enter':
      case 'Tab':
        if (e.shiftKey) return false
        e.preventDefault()
        select(candidates[activeIndex])
        return true
      case 'Escape':
        e.preventDefault()
        e.stopPropagation()
        setDismissedKey(currentKey)
        return true
      default:
        return false
    }
  }

  const popover =
    open && placement
      ? createPortal(
          <div
            id={popoverId}
            className={`${popoverStyles.popover} ${styles.popover}`}
            style={{ top: placement.top, left: placement.left, width: placement.width, maxHeight: placement.maxHeight }}
            data-direction={placement.direction}
            role="listbox"
            aria-label={match?.kind === 'lora' ? t.tagDictionary.assist.loraAriaLabel : t.tagDictionary.assist.ariaLabel}
          >
            {candidates.map((candidate, index) => (
              <button
                key={`${candidate.kind}:${candidate.key}`}
                id={`${popoverId}-option-${index}`}
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                data-active={index === activeIndex}
                className={`${popoverStyles.option} ${styles.option}`}
                ref={(el) => {
                  if (el && index === activeIndex) el.scrollIntoView({ block: 'nearest' })
                }}
                onMouseEnter={() => setActive({ key: currentKey, index })}
                onMouseDown={(e) => {
                  // textarea のフォーカスを外さずに確定する(`@` のポップオーバーと同じ)。
                  e.preventDefault()
                  select(candidate)
                }}
              >
                {candidate.kind === 'tag' ? (
                  <TagSuggestionLabel item={candidate.item} showTranslation={showTranslations} />
                ) : (
                  <span className={styles.lora}>
                    <span className={styles.loraName}>{candidate.lora.name}</span>
                    {candidate.lora.alias && candidate.lora.alias !== candidate.lora.name && (
                      <span className={styles.loraAlias}>{candidate.lora.alias}</span>
                    )}
                  </span>
                )}
              </button>
            ))}
          </div>,
          document.body,
        )
      : null

  return {
    open,
    activeDescendant: open ? `${popoverId}-option-${activeIndex}` : undefined,
    refresh,
    close,
    handleKeyDown,
    textareaProps: {
      onCompositionStart: () => {
        composingRef.current = true
        setMatchIfChanged(null)
      },
      onCompositionEnd: (e) => {
        composingRef.current = false
        refresh(e.currentTarget)
      },
      onBlur: () => setMatchIfChanged(null),
    },
    popover,
  }
}

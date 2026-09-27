/** snippet 内の一致語を `<mark>` で強調する共通コンポーネント(ポップオーバー・検索ページ共用)。 */
import { splitHighlightSegments } from './searchHighlight'

interface HighlightedTextProps {
  text: string
  query: string
}

export function HighlightedText({ text, query }: HighlightedTextProps) {
  const segments = splitHighlightSegments(text, query)
  return (
    <>
      {segments.map((seg, i) =>
        seg.highlighted ? <mark key={i}>{seg.text}</mark> : <span key={i}>{seg.text}</span>,
      )}
    </>
  )
}

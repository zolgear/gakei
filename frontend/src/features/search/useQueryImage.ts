/**
 * 「画像で探す」の手元の画像(`File`)と、その表示用の object URL を持つ。URL は画像を選んだ
 * ときに作り、替えたとき・消したとき・画面を離れたときに手放す(`URL.revokeObjectURL`)。
 * `id` は選び直すたびに増える(同じファイルを選び直しても検索し直すため。クエリのキーに使う)。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export interface QueryImage {
  file: File
  /** 表示用の object URL。 */
  url: string
  id: number
}

export function useQueryImage(): {
  image: QueryImage | null
  setFile: (file: File) => void
  clear: () => void
} {
  const [image, setImage] = useState<QueryImage | null>(null)
  const current = useRef<QueryImage | null>(null)
  const nextId = useRef(1)
  const revokeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const replace = useCallback((next: QueryImage | null) => {
    if (current.current) URL.revokeObjectURL(current.current.url)
    current.current = next
    setImage(next)
  }, [])

  const setFile = useCallback(
    (file: File) => replace({ file, url: URL.createObjectURL(file), id: nextId.current++ }),
    [replace],
  )
  const clear = useCallback(() => replace(null), [replace])

  // 画面を離れたら手放す。StrictMode の付け直し(片付けのすぐ後にもう一度付ける)では手放さない
  // よう、片付けでは次の tick に回し、付け直されたら取り消す。
  useEffect(() => {
    if (revokeTimer.current !== null) {
      clearTimeout(revokeTimer.current)
      revokeTimer.current = null
    }
    return () => {
      revokeTimer.current = setTimeout(() => {
        revokeTimer.current = null
        if (current.current) URL.revokeObjectURL(current.current.url)
        current.current = null
      }, 0)
    }
  }, [])

  return { image, setFile, clear }
}

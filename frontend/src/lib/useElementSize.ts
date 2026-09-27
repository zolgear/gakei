/**
 * 要素の表示サイズ(ResizeObserver)。計測前は null。
 *
 * callback ref で「要素がアタッチされた」こと自体を検知する(`useRef` + 空の依存配列で
 * `useLayoutEffect` を1回だけ回す書き方だと、要素がモード切り替え等で最初のマウント後に
 * 出現するケース(比較ビューのスライダー/並べて表示の切り替えなど)で ResizeObserver が
 * 一度も張られない)。要素そのもの(`T | null`)も返すので、bounding rect の即時参照など
 * imperative なアクセスにも使える。
 */
import { useCallback, useLayoutEffect, useState } from 'react'
import type { Size } from './geometry'

export function useElementSize<T extends HTMLElement>(): [(node: T | null) => void, T | null, Size | null] {
  const [node, setNode] = useState<T | null>(null)
  const [size, setSize] = useState<Size | null>(null)
  const ref = useCallback((el: T | null) => setNode(el), [])

  useLayoutEffect(() => {
    if (!node) return
    const update = () => setSize({ width: node.clientWidth, height: node.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])

  return [ref, node, size]
}

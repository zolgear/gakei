/**
 * グループの先頭だけを出している節の「すべて見る」を押したときの処理(ADR-0054 6章)。含めるグループの
 * 値(グループの id か `none`)を受け取る。無ければ見出しにボタンを出さない。
 */
import { createContext } from 'react'

export const ShowAllGroupContext = createContext<((group: string) => void) | null>(null)

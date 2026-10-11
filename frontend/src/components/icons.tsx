/**
 * 複数の画面で使う小さな線のアイコン(ビューアの操作、最終プロンプトの挿入・置き換え・コピー)。
 * アイコンのライブラリは入れず、既存のツールバーと同じく SVG をインラインで描く。色は
 * `currentColor` に従うので、ボタンの文字色(危険操作の赤など)がそのまま乗る。
 * 意味はボタン側の `aria-label` / `title` で伝えるので、ここでは `aria-hidden` にする。
 */
import type { ReactNode } from 'react'

interface IconProps {
  size?: number
}

function Icon({ size = 14, viewBox = '0 0 16 16', children }: IconProps & { viewBox?: string; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox={viewBox}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

/** 下向きの矢印とトレイ: 原本のダウンロード。 */
export function DownloadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M8 2v8M4.5 6.5L8 10l3.5-3.5M2.5 11v2.5h11V11" />
    </Icon>
  )
}

/** プラス付きの画像: 入力に使う。 */
export function AddToInputIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9 2.5H3.5a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V8" />
      <path d="M2.5 11l3-3 2.5 2.5 1.5-1.5 4 4" />
      <path d="M12.5 1.5v5M10 4h5" />
    </Icon>
  )
}

/** ノードと線: 系列グラフ(アイコンレールの系列のアイコンと同じ形)。 */
export function LineageIcon(props: IconProps) {
  return (
    <Icon {...props} viewBox="0 0 18 18">
      <rect x="2" y="2.5" width="5" height="4" rx="1" />
      <rect x="11" y="7" width="5" height="4" rx="1" />
      <rect x="2" y="11.5" width="5" height="4" rx="1" />
      <path d="M7 4.5c3 0 1.5 4.5 4 4.5M7 13.5c3 0 1.5-4.5 4-4.5" />
    </Icon>
  )
}

/** 丸に「i」: Generated(Run)の詳細を開く。 */
export function RunDetailIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 7.25v4" />
      <path d="M8 4.75v.01" strokeWidth="2" />
    </Icon>
  )
}

/** 鎖の輪: 共有リンク。 */
export function ShareLinkIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6.8 9.2a2.6 2.6 0 0 0 3.7 0l2.4-2.4a2.6 2.6 0 0 0-3.7-3.7l-.9.9" />
      <path d="M9.2 6.8a2.6 2.6 0 0 0-3.7 0L3.1 9.2a2.6 2.6 0 0 0 3.7 3.7l.9-.9" />
    </Icon>
  )
}

/** 箱と下向きの矢印: 系列を ZIP に書き出す(ADR-0037)。 */
export function ExportArchiveIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2.5 6h11v7.5h-11zM3.5 3.5h9L13.5 6h-11z" />
      <path d="M8 7.5v4M6.3 9.8L8 11.5l1.7-1.7" />
    </Icon>
  )
}

/** ゴミ箱: 削除。 */
export function TrashIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2.5 4h11M6 4V2.5h4V4M4 4l.7 9.5h6.6L12 4M6.8 6.5v4.5M9.2 6.5v4.5" />
    </Icon>
  )
}

/** テキストの行と差し込む矢印: プロンプトに挿入。 */
export function InsertTextIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2 3h12M8 13.5h6M2 13.5h1.5" />
      <path d="M2 8.25h5.5M5.5 6l2.25 2.25L5.5 10.5" />
      <path d="M10 6v4.5" />
    </Icon>
  )
}

/** 逆向きの2本の矢印: 置き換え。 */
export function ReplaceIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2.5 5.5h10M10 3l2.5 2.5L10 8" />
      <path d="M13.5 10.5h-10M6 8l-2.5 2.5L6 13" />
    </Icon>
  )
}

/** 重なった四角: コピー。 */
export function CopyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="5.5" y="5.5" width="8" height="8" rx="1" />
      <path d="M10.5 3.5V3a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1v6.5a1 1 0 0 0 1 1h.5" />
    </Icon>
  )
}

/** チェック: 操作が済んだ(コピーしました)。 */
export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 8.5l3.2 3.2L13 4.5" />
    </Icon>
  )
}

/** バツ: 操作が失敗した(コピーできませんでした)。 */
export function CrossIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </Icon>
  )
}

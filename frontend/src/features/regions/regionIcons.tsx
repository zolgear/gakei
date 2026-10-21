/** 「領域」の画面のアイコン(14px)。配置(`compositeIcons.tsx`)と同じ線の太さ・形の作り。 */

const common = {
  width: 14,
  height: 14,
  viewBox: '0 0 16 16',
  fill: 'none',
  'aria-hidden': true,
} as const

const stroke = {
  stroke: 'currentColor',
  strokeWidth: 1.4,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
} as const

/** 「領域」(番号付きの四角)。 */
export function RegionsIcon({ size = 14 }: { size?: number }) {
  return (
    <svg {...common} width={size} height={size}>
      <rect x="1.5" y="1.5" width="8" height="7" rx="0.5" {...stroke} />
      <rect x="6.5" y="7.5" width="8" height="7" rx="0.5" strokeDasharray="2 1.6" {...stroke} />
      <path d="M3.5 3.5h2" {...stroke} />
    </svg>
  )
}

export function ZoomInIcon() {
  return (
    <svg {...common}>
      <circle cx="7" cy="7" r="4.5" {...stroke} />
      <path d="M10.5 10.5 14 14M5 7h4M7 5v4" {...stroke} />
    </svg>
  )
}

export function ZoomOutIcon() {
  return (
    <svg {...common}>
      <circle cx="7" cy="7" r="4.5" {...stroke} />
      <path d="M10.5 10.5 14 14M5 7h4" {...stroke} />
    </svg>
  )
}

export function PanIcon() {
  return (
    <svg {...common}>
      <path d="M8 1.5v13M1.5 8h13M8 1.5 6.3 3.2M8 1.5l1.7 1.7M8 14.5l-1.7-1.7M8 14.5l1.7-1.7M1.5 8l1.7-1.7M1.5 8l1.7 1.7M14.5 8l-1.7-1.7M14.5 8l-1.7 1.7" {...stroke} />
    </svg>
  )
}

export function ArrowUpIcon() {
  return (
    <svg {...common} width={12} height={12}>
      <path d="M4 10l4-4 4 4" {...stroke} />
    </svg>
  )
}

export function ArrowDownIcon() {
  return (
    <svg {...common} width={12} height={12}>
      <path d="M4 6l4 4 4-4" {...stroke} />
    </svg>
  )
}

/** プレビューの重ね描きを出す・隠す。 */
export function EyeIcon({ off = false, size = 14 }: { off?: boolean; size?: number }) {
  return (
    <svg {...common} width={size} height={size}>
      <path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8Z" {...stroke} />
      <circle cx="8" cy="8" r="2" {...stroke} />
      {off && <path d="M2.5 13.5 13.5 2.5" {...stroke} />}
    </svg>
  )
}

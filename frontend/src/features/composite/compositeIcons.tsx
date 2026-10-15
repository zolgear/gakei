/** 配置の編集画面のアイコン(14px)。ADR-0010 のスケッチと同じく、ラベルを置かずツールチップで示す。 */

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

export function ArrangeIcon({ size = 14 }: { size?: number }) {
  return (
    <svg {...common} width={size} height={size}>
      <rect x="1.5" y="3" width="7" height="7" rx="1" {...stroke} />
      <path d="M8.5 6.5 14 5.5v7l-6.5 1.5V10" {...stroke} />
    </svg>
  )
}

export function AddLayerIcon() {
  return (
    <svg {...common}>
      <path d="M8 3v10M3 8h10" {...stroke} />
    </svg>
  )
}

export function TransformModeIcon() {
  return (
    <svg {...common}>
      <rect x="3.5" y="3.5" width="9" height="9" {...stroke} />
      <circle cx="3.5" cy="3.5" r="1.4" fill="currentColor" />
      <circle cx="12.5" cy="3.5" r="1.4" fill="currentColor" />
      <circle cx="12.5" cy="12.5" r="1.4" fill="currentColor" />
      <circle cx="3.5" cy="12.5" r="1.4" fill="currentColor" />
    </svg>
  )
}

export function DistortModeIcon() {
  return (
    <svg {...common}>
      <path d="M5.5 3.5h5l3 9h-11z" {...stroke} />
      <circle cx="5.5" cy="3.5" r="1.4" fill="currentColor" />
      <circle cx="10.5" cy="3.5" r="1.4" fill="currentColor" />
      <circle cx="13.5" cy="12.5" r="1.4" fill="currentColor" />
      <circle cx="2.5" cy="12.5" r="1.4" fill="currentColor" />
    </svg>
  )
}

export function FlipXIcon() {
  return (
    <svg {...common}>
      <path d="M8 1.5v13" {...stroke} strokeDasharray="1.5 1.5" />
      <path d="M6 4 2 12h4zM10 4l4 8h-4z" {...stroke} />
    </svg>
  )
}

export function FlipYIcon() {
  return (
    <svg {...common}>
      <path d="M1.5 8h13" {...stroke} strokeDasharray="1.5 1.5" />
      <path d="M4 6 12 2v4zM4 10l8 4v-4z" {...stroke} />
    </svg>
  )
}

export function ForwardIcon() {
  return (
    <svg {...common}>
      <rect x="2" y="6" width="7" height="7" rx="1" {...stroke} />
      <rect x="7" y="2" width="7" height="7" rx="1" fill="currentColor" fillOpacity="0.35" {...stroke} />
    </svg>
  )
}

export function BackwardIcon() {
  return (
    <svg {...common}>
      <rect x="7" y="2" width="7" height="7" rx="1" {...stroke} />
      <rect x="2" y="6" width="7" height="7" rx="1" fill="currentColor" fillOpacity="0.35" {...stroke} />
    </svg>
  )
}

export function DuplicateIcon() {
  return (
    <svg {...common}>
      <rect x="5" y="5" width="8.5" height="8.5" rx="1" {...stroke} />
      <path d="M3 10.5H2.5v-8h8V3" {...stroke} />
    </svg>
  )
}

export function DeleteIcon() {
  return (
    <svg {...common}>
      <path
        d="M3 4.5h10M6.5 4.5V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1.5M4.5 4.5v8a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1v-8"
        {...stroke}
      />
    </svg>
  )
}

export function PrimaryIcon() {
  return (
    <svg {...common}>
      <path d="m8 2 1.8 3.8 4.2.5-3.1 2.9.8 4.1L8 11.3l-3.7 2 .8-4.1L2 6.3l4.2-.5z" {...stroke} />
    </svg>
  )
}

export function FitIcon() {
  return (
    <svg {...common}>
      <path d="M2 5.5V2h3.5M10.5 2H14v3.5M14 10.5V14h-3.5M5.5 14H2v-3.5" {...stroke} />
      <rect x="5" y="5" width="6" height="6" {...stroke} />
    </svg>
  )
}

export function CenterIcon() {
  return (
    <svg {...common}>
      <rect x="2" y="2" width="12" height="12" rx="1" {...stroke} />
      <path d="M8 5v6M5 8h6" {...stroke} />
    </svg>
  )
}

export function UndoIcon() {
  return (
    <svg {...common}>
      <path d="M4 4.5H9a4 4 0 1 1 0 8H6" {...stroke} strokeWidth={1.5} />
      <path d="M6.5 2 4 4.5l2.5 2.5" {...stroke} strokeWidth={1.5} />
    </svg>
  )
}

export function RedoIcon() {
  return (
    <svg {...common}>
      <path d="M12 4.5H7a4 4 0 1 0 0 8h3" {...stroke} strokeWidth={1.5} />
      <path d="M9.5 2 12 4.5 9.5 7" {...stroke} strokeWidth={1.5} />
    </svg>
  )
}

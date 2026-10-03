/**
 * GAKEI がブラウザ(localStorage / sessionStorage)に置くキーの一覧と、それをまとめて消す処理
 * (設定画面「表示」の「ブラウザに保存した設定」。ADR-0031 1章・2026-10-04 追記)。
 *
 * GAKEI のキーは `gakei.` か `gakei:` で始める(どちらの綴りも使っている)。消すのはこの接頭辞の
 * キーだけで、同じオリジンにある他のキーには触れない。新しいキーを足したら `GAKEI_STORAGE_KEYS`
 * にも足す(`browserStorage.test.ts` が、ソース中の `'gakei…'` のキーが一覧に無いと落ちる)。
 *
 * どの読み書きも、使えない環境(プライベートブラウジング、サイトデータの拒否)で例外を投げうるので
 * try/catch で囲む。
 */

/** テストで差し替えられるよう、使う操作だけに絞った Storage。 */
export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>

/** GAKEI のキーの接頭辞。 */
export const GAKEI_STORAGE_PREFIXES = ['gakei.', 'gakei:'] as const

/** GAKEI が使う(使っていた)キー。値は何を覚えているか(開発者向けのメモ。画面には出さない)。 */
export const GAKEI_STORAGE_KEYS = {
  'gakei.locale': '表示言語',
  'gakei.faviconProgress': 'タブのアイコンで進捗を示すか',
  'gakei:studio-layout': '生成画面のレイアウト(下段 / サイドバー)',
  'gakei:studio-split-ratio': '生成画面の上下の高さの比率',
  'gakei:studio-input-width': 'サイドバー配置での入力欄の幅',
  'gakei:sidebar-width': 'サイドバーの幅',
  'gakei:selected-panel': 'サイドバーで開いているパネル',
  'gakei:stock-group-open': 'ストックの各グループの開閉',
  'gakei:stock-groups-open': '(旧)ストックのグループの開閉',
  'gakei:viewer-similar-open': 'ビューアの「似た画像」の開閉',
  'gakei:map-prefs': 'マップの表示・近傍の数・上限・しきい値・系列の辺・詳細パネルの開閉',
  'gakei.sketch.prefs': 'スケッチのペン色と太さ',
  'gakei.runForm.seedMode': 'シードのランダム / 固定',
  'gakei.runForm.v1': '生成フォームの下書き(プロンプト・パラメーター・入力画像)',
  'gakei:last-asset-group': '生成フォームで最後に選んだグループ',
} as const satisfies Record<string, string>

export function isGakeiStorageKey(key: string): boolean {
  return GAKEI_STORAGE_PREFIXES.some((prefix) => key.startsWith(prefix))
}

/** `localStorage` を返す。使えない(アクセスで例外になる)環境では null。 */
export function safeLocalStorage(): StorageLike | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export function safeSessionStorage(): StorageLike | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage
  } catch {
    return null
  }
}

/** そのストレージにある GAKEI のキー。 */
export function listGakeiStorageKeys(storage: StorageLike): string[] {
  const keys: string[] = []
  try {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (key !== null && isGakeiStorageKey(key)) keys.push(key)
    }
  } catch {
    // 読めなければ消すものも無い。
  }
  return keys
}

/**
 * GAKEI のキーをすべて消し、消した数を返す。消している途中で添字がずれないよう、先に一覧を作る。
 * 1つ消せなくても残りは続ける。
 */
export function clearGakeiStorage(storages: readonly (StorageLike | null)[]): number {
  let removed = 0
  for (const storage of storages) {
    if (!storage) continue
    for (const key of listGakeiStorageKeys(storage)) {
      try {
        storage.removeItem(key)
        removed++
      } catch {
        // 次のキーへ。
      }
    }
  }
  return removed
}

/** localStorage と sessionStorage の GAKEI のキーを消す(画面から使う)。 */
export function clearAllGakeiBrowserStorage(): number {
  return clearGakeiStorage([safeLocalStorage(), safeSessionStorage()])
}

/**
 * GAKEI のキーを消してページを読み込み直す。生成フォームは離れるとき(`pagehide`・
 * `visibilitychange`)に下書きを書き直すので、そのあとにもう一度消す(リスナーは登録順に
 * 呼ばれるので、後から足したこちらが最後に動く)。
 */
export function clearGakeiBrowserStorageAndReload(): void {
  clearAllGakeiBrowserStorage()
  const clearAgain = () => {
    clearAllGakeiBrowserStorage()
  }
  window.addEventListener('pagehide', clearAgain)
  document.addEventListener('visibilitychange', clearAgain)
  window.location.reload()
}

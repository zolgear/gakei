// 第三者ライセンス表記の生成のうち、ファイル I/O を伴わない純粋な部分だけを集めたモジュール
// (frontend/scripts/third-party-notices.mjs から使う。ESM のみで、vitest から直接
// テストできるよう分離した。tsc の対象(`tsconfig.app.json` の include は `src` のみ)には
// 入らないので、テストは `src/scripts/thirdPartyNotices.test.ts` に置き、ここを相対 import する)。

/**
 * package-lock.json の `packages` の1エントリを、表記の対象にするかどうか判定する。
 * ルート(キーが空文字。プロジェクト自身)と `dev: true` は対象外。`optional`/`peer` は
 * 対象に含める(ADR-0021 4章)。
 * @param {string} key
 * @param {{ dev?: boolean }} entry
 * @returns {boolean}
 */
export function isNoticeTarget(key, entry) {
  if (key === '') return false
  return !entry?.dev
}

/**
 * `node_modules/foo/node_modules/@scope/bar` のようなキーから、実際のパッケージ名
 * (この例では `@scope/bar`)を取り出す。最後の `node_modules/` の後ろがパッケージ名。
 * @param {string} key
 * @returns {string}
 */
export function packageNameFromKey(key) {
  const marker = 'node_modules/'
  const lastIndex = key.lastIndexOf(marker)
  return key.slice(lastIndex + marker.length)
}

/**
 * `package.json` の `license` を表記用の文字列にする。文字列でなければ
 * (古い形式の) `licenses[0].type` を見る。どちらも無ければ `UNKNOWN`。
 * @param {{ license?: unknown, licenses?: Array<{ type?: string }> }} pkg
 * @returns {string}
 */
export function licenseOf(pkg) {
  if (typeof pkg.license === 'string' && pkg.license.trim()) return pkg.license.trim()
  const fromLicenses = pkg.licenses?.[0]?.type
  if (typeof fromLicenses === 'string' && fromLicenses.trim()) return fromLicenses.trim()
  return 'UNKNOWN'
}

/**
 * `package.json` の `homepage` → `repository.url`(文字列 or `{ url }`)の順で URL を取る。
 * @param {{ homepage?: unknown, repository?: unknown }} pkg
 * @returns {string | null}
 */
export function urlOf(pkg) {
  if (typeof pkg.homepage === 'string' && pkg.homepage.trim()) return pkg.homepage.trim()
  const repository = pkg.repository
  if (typeof repository === 'string' && repository.trim()) return repository.trim()
  if (repository && typeof repository === 'object' && typeof repository.url === 'string') {
    return repository.url.trim() || null
  }
  return null
}

/**
 * 1パッケージ分の表記のブロック(見出し行 + ライセンス本文)を作る。区切り線(`----`)は
 * 呼び出し側(`renderFrontendNotices`)が項目間に挿む。
 * @param {{ name: string, version: string, license: string, url: string | null, texts: string[] }} entry
 * @returns {string}
 */
export function formatNoticeEntry({ name, version, license, url, texts }) {
  const header = `${name}@${version} — ${license} — ${url ?? 'n/a'}`
  const body = texts.length > 0 ? texts.join('\n\n') : '(no license file in the package)'
  return `${header}\n\n${body}`
}

/**
 * 全パッケージ分をまとめた `dist/third-party-notices.txt` の中身。生成日時は入れない
 * (依存構成が同じなら常に同じ出力になる、再現性のため)。
 * @param {Array<{ name: string, version: string, license: string, url: string | null, texts: string[] }>} entries
 * @returns {string}
 */
export function renderFrontendNotices(entries) {
  const body = entries.map(formatNoticeEntry).join('\n----\n')
  return `GAKEI frontend third-party notices\n\n${body}\n`
}

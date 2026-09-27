// `npm run build` の最後に走らせ、`dist/third-party-notices.txt` を作る(ADR-0021 4章)。
// 対象は package-lock.json の `packages` のうち dev でないもの(ルートを除く)。ライセンス名は
// 各パッケージの package.json の `license`、本文は `LICENSE*` / `LICENCE*` / `COPYING*`
// (大文字小文字を区別しない)を全部連結する。純粋な判定・整形部分は
// `thirdPartyNotices.lib.mjs` に分けてある(vitest からテストするため)。
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  isNoticeTarget,
  licenseOf,
  packageNameFromKey,
  renderFrontendNotices,
  urlOf,
} from './thirdPartyNotices.lib.mjs'

const __dirname = dirname(fileURLToPath(import.meta.url))
const frontendRoot = resolve(__dirname, '..')
const lockPath = join(frontendRoot, 'package-lock.json')
const distDir = join(frontendRoot, 'dist')
const outFile = join(distDir, 'third-party-notices.txt')

const LICENSE_FILE_RE = /^(license|licence|copying)/i

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf-8'))
}

/** 同じディレクトリの LICENSE* / LICENCE* / COPYING* を名前順に全部読んで連結用の配列にする。 */
function licenseTexts(packageDir) {
  let names
  try {
    names = readdirSync(packageDir)
  } catch {
    return []
  }
  return names
    .filter((name) => LICENSE_FILE_RE.test(name))
    .sort()
    .map((name) => readFileSync(join(packageDir, name), 'utf-8'))
}

function collectEntries() {
  const lock = readJson(lockPath)
  const packages = lock.packages ?? {}
  // 同名・同バージョンのパッケージがネストして複数回現れることがある(hoist されなかった
  // 依存)ので、キー(name@version)で重複を除く。
  const byKey = new Map()

  for (const [key, entry] of Object.entries(packages)) {
    if (!isNoticeTarget(key, entry)) continue

    const name = packageNameFromKey(key)
    const packageDir = join(frontendRoot, key)
    const packageJsonPath = join(packageDir, 'package.json')
    if (!existsSync(packageJsonPath)) {
      // package-lock.json には載っているがインストールされていない(プラットフォーム違いの
      // optional 依存など)。表記のしようがないので飛ばす。
      console.warn(`[third-party-notices] skip (not installed): ${key}`)
      continue
    }

    const pkg = readJson(packageJsonPath)
    const version = pkg.version ?? entry.version ?? 'unknown'
    const dedupeKey = `${name}@${version}`
    if (byKey.has(dedupeKey)) continue

    byKey.set(dedupeKey, {
      name,
      version,
      license: licenseOf(pkg),
      url: urlOf(pkg),
      texts: licenseTexts(packageDir),
    })
  }

  return [...byKey.values()].sort((a, b) => {
    if (a.name !== b.name) return a.name < b.name ? -1 : 1
    return a.version < b.version ? -1 : a.version > b.version ? 1 : 0
  })
}

function main() {
  if (!existsSync(distDir)) {
    console.error(
      '[third-party-notices] dist/ が見つかりません。`vite build` の後に実行してください。',
    )
    process.exit(1)
  }

  const entries = collectEntries()
  writeFileSync(outFile, renderFrontendNotices(entries), 'utf-8')
  console.log(`[third-party-notices] 書き出しました: ${outFile} (${entries.length} packages)`)
}

main()

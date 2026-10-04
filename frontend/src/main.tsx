import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider, createBrowserRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
// フォントは Google Fonts の CDN ではなく npm 同梱(オフラインでも表示崩れしないように)。
// 400/600 の2ウェイトだけ import する(500 は使用箇所を 400/600 に寄せて廃止)。
// `japanese-400.css` のような単一ファイルではなく無印の `400.css` を使う。これは
// unicode-range で百十数個の小さなチャンクに分割された形式で、実際に画面に出す文字の分だけ
// ブラウザが個別に取得する(日本語UIで実際に使う漢字・仮名は全体のごく一部のため、
// 実効ダウンロード量は japanese-400.css 単体(~900KB)よりずっと小さくなる)。
import '@fontsource/ibm-plex-sans-jp/400.css'
import '@fontsource/ibm-plex-sans-jp/600.css'
import '@fontsource/jetbrains-mono/400.css'
import './index.css'
import { App } from './App'
import { RunFormProvider } from './context/RunFormContext'
import { LineageOriginProvider } from './context/LineageOriginProvider'
import { StudioReturnProvider } from './context/StudioReturnProvider'
import { initLocale } from './i18n'
import { LocaleRoot } from './i18n/LocaleRoot'
import { AuthGate } from './features/auth/AuthGate'
import { PublicSharePage } from './features/share/PublicSharePage'
import { parsePublicSharePath } from './features/share/publicSharePath'
import { publicShareViewFromLocation } from './features/share/publicShareView'

// 最初の描画より前に表示言語を決める(ADR-0015)。
initLocale()

const queryClient = new QueryClient()

// ログイン不要の共有のページ(`/s/{トークン}`。ADR-0029)は、`AuthGate` とルーティングの外で描く
// (`/api/auth/me` を呼ばず、App バーなどログイン前提の画面も出さない)。`/s/{トークン}/runs/{run_id}`
// (Run の詳細の直リンク)と `/s/{トークン}/lineage`(全画面の系列グラフ)も同じ。再読み込みでも
// 全画面を閉じたときに戻る Run を保つよう、履歴の state も読む。
const sharePath = parsePublicSharePath(window.location.pathname)
const shareView = publicShareViewFromLocation(window.location.pathname, window.history.state)

// ルーターはデータルーター(`createBrowserRouter`)にする。設定画面の「保存していない変更があります」
// の確認に使う `useBlocker` がデータルーターでしか動かないため(ADR-0031 4章)。ルートの定義は
// これまでどおり `App.tsx` の `<Routes>` に置き、ここでは `path: '*'` の1ルートでそのまま包む。
// 共有のページではルーターを使わないので作らない(履歴の監視も始めない)。
const router =
  sharePath === null
    ? createBrowserRouter([
        {
          path: '*',
          element: (
            <RunFormProvider>
              <LineageOriginProvider>
                <StudioReturnProvider>
                  <App />
                </StudioReturnProvider>
              </LineageOriginProvider>
            </RunFormProvider>
          ),
        },
      ])
    : null

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LocaleRoot>
        {sharePath !== null ? (
          <PublicSharePage
            token={sharePath.token}
            initialRunId={shareView?.runId ?? null}
            initialLineage={shareView?.lineage ?? false}
          />
        ) : (
          /* oidc モードの未ログインはここで足止めする(ADR-0019)。ログイン画面は
             ルーティングを必要としないのでルーターの外側に置く。 */
          <AuthGate>{router && <RouterProvider router={router} />}</AuthGate>
        )}
      </LocaleRoot>
    </QueryClientProvider>
  </StrictMode>,
)

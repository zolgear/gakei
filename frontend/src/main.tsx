import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
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

// 最初の描画より前に表示言語を決める(ADR-0015)。
initLocale()

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <LocaleRoot>
        {/* oidc モードの未ログインはここで足止めする(ADR-0019)。ログイン画面は
            ルーティングを必要としないので BrowserRouter の外側に置く。 */}
        <AuthGate>
          <BrowserRouter>
            <RunFormProvider>
              <LineageOriginProvider>
                <StudioReturnProvider>
                  <App />
                </StudioReturnProvider>
              </LineageOriginProvider>
            </RunFormProvider>
          </BrowserRouter>
        </AuthGate>
      </LocaleRoot>
    </QueryClientProvider>
  </StrictMode>,
)

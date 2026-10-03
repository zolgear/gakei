import { Navigate, Route, Routes, useParams } from 'react-router'
import { AppShell } from './shell/AppShell'
import { HistoryPage } from './pages/HistoryPage'
import { StudioWorkspace } from './features/workspace/StudioWorkspace'
import { AssetViewerPage } from './pages/AssetViewerPage'
import { RunDetailPage } from './pages/RunDetailPage'
import { ComparePage } from './pages/ComparePage'
import { LineagePage } from './pages/LineagePage'
import { SearchPage } from './pages/SearchPage'
import { DuplicatesPage } from './pages/DuplicatesPage'
import { SettingsPage } from './pages/SettingsPage'

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<HistoryPage />} />
        <Route path="/studio" element={<StudioWorkspace />} />
        {/* 生成/編集は UI 上で区別しない(ADR-0009)。旧ルートはブックマーク等のため残す。 */}
        <Route path="/generate" element={<Navigate to="/studio" replace />} />
        <Route path="/edit" element={<Navigate to="/studio" replace />} />
        <Route path="/runs/:id" element={<RunDetailPage />} />
        <Route path="/runs/:runId/compare" element={<ComparePage />} />
        <Route path="/assets/:id" element={<AssetViewerPage />} />
        <Route path="/lineage/:assetId" element={<LineagePage />} />
        <Route path="/search" element={<SearchPage />} />
        {/* 重複の候補(ADR-0033 8章)。ストックのパネルからたどる。 */}
        <Route path="/stock/duplicates" element={<DuplicatesPage />} />
        {/*
          設定はページに分ける(ADR-0031)。`/settings` は目次(広いときは「表示」も)。
          ComfyUI のワークフローの登録・編集も設定の枠の中で開く(目次は ComfyUI を選んだ状態)。
          どれも `SettingsPage` が描き分ける(子のルートの間を移っても枠とトーストを作り直さない)。
          管理者設定のページを非管理者が開いたときは、`SettingsPage` が `/settings` に戻す(ADR-0019 5章)。
        */}
        <Route path="/settings" element={<SettingsPage />}>
          <Route index element={null} />
          <Route path=":page" element={null} />
          <Route path="comfyui/workflows/new" element={null} />
          <Route path="comfyui/workflows/:id" element={null} />
        </Route>
        {/* ワークフローの一覧は ComfyUI のページの一節になった(ADR-0031 1章の 2026-10-01 追記)。 */}
        <Route path="/settings/comfyui/workflows" element={<Navigate to="/settings/comfyui" replace />} />
        {/* ADR-0031 より前のパス。ブックマーク等のため新しいパスへ転送する。 */}
        <Route path="/settings/comfyui/new" element={<Navigate to="/settings/comfyui/workflows/new" replace />} />
        <Route path="/settings/comfyui/:id" element={<LegacyWorkflowRedirect />} />
      </Route>
    </Routes>
  )
}

/** `/settings/comfyui/:id`(ADR-0031 より前のワークフロー編集のパス)を新しいパスへ転送する。 */
function LegacyWorkflowRedirect() {
  const { id } = useParams()
  return <Navigate to={`/settings/comfyui/workflows/${id ?? ''}`} replace />
}

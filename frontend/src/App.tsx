import { Navigate, Route, Routes } from 'react-router'
import { AppShell } from './shell/AppShell'
import { HistoryPage } from './pages/HistoryPage'
import { StudioWorkspace } from './features/workspace/StudioWorkspace'
import { AssetViewerPage } from './pages/AssetViewerPage'
import { RunDetailPage } from './pages/RunDetailPage'
import { ComparePage } from './pages/ComparePage'
import { LineagePage } from './pages/LineagePage'
import { SearchPage } from './pages/SearchPage'
import { SettingsPage } from './pages/SettingsPage'
import { ComfyUIWorkflowsListPage } from './features/comfyui-workflows/ComfyUIWorkflowsListPage'
import { ComfyUIWorkflowFormPage } from './features/comfyui-workflows/ComfyUIWorkflowFormPage'
import { RequireAdmin } from './features/auth/RequireAdmin'

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
        <Route path="/settings" element={<SettingsPage />} />
        {/* ワークフローの登録は管理者設定(ADR-0019 5章)。非管理者は /settings に戻す。 */}
        <Route
          path="/settings/comfyui"
          element={
            <RequireAdmin>
              <ComfyUIWorkflowsListPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/settings/comfyui/new"
          element={
            <RequireAdmin>
              <ComfyUIWorkflowFormPage />
            </RequireAdmin>
          }
        />
        <Route
          path="/settings/comfyui/:id"
          element={
            <RequireAdmin>
              <ComfyUIWorkflowFormPage />
            </RequireAdmin>
          }
        />
      </Route>
    </Routes>
  )
}

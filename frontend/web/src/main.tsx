import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { adoptTokenFromUrl } from './api';
import { I18nProvider, useI18n } from './i18n';
import { WorkspaceErrorBoundary } from './components/WorkspaceErrorBoundary';
import { queryRetryPolicy } from './hooks';
import { installStaleChunkRecovery } from './lib/preloadRecovery';
import { RELEASE_ID } from '../../core/src/release.generated';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import 'katex/dist/katex.min.css';
import './index.css';

const isAdminData = window.location.pathname === '/admin/data' || window.location.pathname.startsWith('/admin/data/');
const App = lazy(() => import('./App'));
const AdminDataApp = lazy(() => import('./admin-data/AdminDataApp'));

// A cockpit left open across an update can still reference a deleted hashed
// chunk. Reload the no-store shell before React turns that import into a blank UI.
installStaleChunkRecovery(window, () => window.location.reload(), {
  buildId: RELEASE_ID,
  storage: () => window.sessionStorage,
});

// Runs before the first request so a QR-paired phone is authenticated for
// every later load, not just the one carrying `?token=`.
if (!isAdminData) adoptTokenFromUrl();

const embeddedDesktop = window.parent !== window;
document.documentElement.dataset.argusEmbedded = String(embeddedDesktop);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 3_000, retry: queryRetryPolicy, refetchOnWindowFocus: false },
  },
});

function WebApp() {
  const { locale } = useI18n();
  return (
    <>
      <WorkspaceErrorBoundary locale={locale}>
        <Suspense fallback={<div className="grid min-h-screen place-items-center text-sm text-ink-faint">{locale === 'zh-CN' ? '正在加载工作台…' : 'Loading workbench…'}</div>}>
          {isAdminData ? <AdminDataApp /> : <App />}
        </Suspense>
      </WorkspaceErrorBoundary>
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <WebApp />
      </I18nProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);

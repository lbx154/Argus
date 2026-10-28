import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { adoptTokenFromUrl, api, isAuthenticationError } from './api';
import { ConnectionProblemBanner } from './components/ConnectionProblemBanner';
import { errorText } from './lib/format';
import { Capabilities } from './lib/capabilities';
import { APPLICATION_WORKFLOWS } from '../../core/src/applicationWorkflows';
import { I18nProvider, useI18n } from './i18n';
import { WorkspaceErrorBoundary } from './components/WorkspaceErrorBoundary';
import { PageUpdateNotice } from './components/PageUpdateNotice';
import { queryRetryPolicy } from './hooks';
import { installStaleChunkRecovery } from './lib/preloadRecovery';
import { RELEASE_ID } from '../../core/src/protocol';
import { persistQueryCache, restoreQueryCache } from './lib/queryPersistence';
import { loadApplicationBrand } from './applicationBrand';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import 'katex/dist/katex.min.css';
import './index.css';

const isAdminData = window.location.pathname === '/admin/data' || window.location.pathname.startsWith('/admin/data/');
const App = lazy(() => import('./App'));
const WorkflowWorkbench = lazy(() => import('./components/WorkflowWorkbench'));
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
    // Keep a project's views while the operator works elsewhere, so coming
    // back draws the last known content at once and refreshes it behind.
    queries: { staleTime: 3_000, gcTime: 30 * 60_000, retry: queryRetryPolicy, refetchOnWindowFocus: false },
  },
});
// A reload in this tab starts from the views it last showed.
if (!isAdminData) {
  const storage = () => window.sessionStorage;
  restoreQueryCache(queryClient, storage, RELEASE_ID);
  persistQueryCache(queryClient, storage, RELEASE_ID);
}

function WorkbenchEntry() {
  const meta = useQuery({ queryKey: ['api-meta'], queryFn: api.meta });
  const { locale } = useI18n();
  // Without an access link nothing else can load: show the pairing card itself.
  if (meta.error && isAuthenticationError(meta.error)) {
    return <ConnectionProblemBanner error={meta.error} onRetry={() => void meta.refetch()} />;
  }
  if (meta.error) return <div role="alert" className="p-8">
    {errorText(meta.error)}<button className="ml-4 underline" onClick={() => void meta.refetch()}>
      {locale === 'zh-CN' ? '重新连接' : 'Reconnect'}
    </button>
  </div>;
  if (!meta.data) return <div className="p-8">{locale === 'zh-CN' ? '正在连接…' : 'Connecting…'}</div>;
  return <Capabilities.Provider value={meta.data.capabilities}>
    {meta.data.capabilities.includes(APPLICATION_WORKFLOWS) && new URLSearchParams(window.location.search).get('legacy') !== '1'
      ? <WorkflowWorkbench /> : <App />}
  </Capabilities.Provider>;
}

function WebApp() {
  const { locale } = useI18n();
  return (
    <>
      <PageUpdateNotice />
      <WorkspaceErrorBoundary locale={locale}>
        <Suspense fallback={<div className="grid min-h-screen place-items-center text-sm text-ink-faint">{locale === 'zh-CN' ? '正在加载工作台…' : 'Loading workbench…'}</div>}>
          {isAdminData ? <AdminDataApp /> : <WorkbenchEntry />}
        </Suspense>
      </WorkspaceErrorBoundary>
    </>
  );
}

void (isAdminData ? Promise.resolve() : loadApplicationBrand()).then(() => ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <WebApp />
      </I18nProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)).catch((error: unknown) => {
  const root = document.getElementById('root')!;
  root.setAttribute('role', 'alert');
  root.textContent = `Cannot open workbench: ${error instanceof Error ? error.message : String(error)}`;
});

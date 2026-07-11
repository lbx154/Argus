import React, { Suspense, lazy, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BootSplash } from './components/BootSplash';
import '@fontsource-variable/geist';
import '@fontsource-variable/geist-mono';
import '@fontsource-variable/noto-sans-sc';
import './index.css';

const App = lazy(() => import('./App'));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 3_000, retry: 1, refetchOnWindowFocus: false },
  },
});

function WebApp() {
  const [booting, setBooting] = useState(true);
  return (
    <>
      <Suspense fallback={null}>
        <App />
      </Suspense>
      {booting ? <BootSplash onDone={() => setBooting(false)} /> : null}
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <WebApp />
    </QueryClientProvider>
  </React.StrictMode>,
);

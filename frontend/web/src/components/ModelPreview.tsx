import { lazy, Suspense } from 'react';
import { useI18n } from '../i18n';
import type { ModelViewerProps } from './model/ModelViewer';

// three.js and the model loaders load only when a model is shown. Declared
// once at module level so the lazy component keeps its identity across renders.
const ModelViewer = lazy(() => import('./model/ModelViewer'));

function Loading({ thumbnail }: { thumbnail?: boolean }) {
  const { locale } = useI18n();
  return <div className={thumbnail ? 'flex h-[220px] w-full items-center justify-center rounded-md bg-sunken/60 text-xs text-ink-faint' : 'm-auto p-6 text-sm text-ink-faint'} role="status">
    {locale === 'zh-CN' ? '正在打开 3D 查看器…' : 'Opening the 3D viewer…'}
  </div>;
}

/** Interactive 3D view of a URDF robot or a mesh file. */
export function ModelPreview(props: Omit<ModelViewerProps, 'mode' | 'onOpen'>) {
  return <Suspense fallback={<Loading />}><ModelViewer {...props} mode="full" /></Suspense>;
}

/** A small, slowly turning view of a model for a result card; a click opens the full viewer. */
export function ModelThumbnail(props: Omit<ModelViewerProps, 'mode' | 'constraintFiles'>) {
  return <Suspense fallback={<Loading thumbnail />}><ModelViewer {...props} mode="thumbnail" /></Suspense>;
}

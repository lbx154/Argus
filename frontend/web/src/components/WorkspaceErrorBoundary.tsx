import { Component, type ReactNode } from 'react';
import type { Locale } from '../i18n';
import { applicationBrand } from '../applicationBrand';

/** A failed lazy page must not take the entire cockpit down or reload forever. */
export class WorkspaceErrorBoundary extends Component<{ children: ReactNode; locale: Locale }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    const zh = this.props.locale === 'zh-CN';
    return (
      <main className="flex min-h-dvh items-center justify-center bg-bg p-8 text-ink" role="alert">
        <section className="max-w-lg rounded-xl border border-line bg-panel p-8 shadow-lg">
          <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-blue">{applicationBrand().name}</p>
          <h1 className="text-lg font-semibold">{zh ? '工作台暂时无法显示' : 'The workspace could not be displayed'}</h1>
          <p className="mt-3 text-sm leading-relaxed text-ink-dim">
            {zh ? '页面资源没能加载，多半是 Argus 刚换了版本。重新加载即可，没发出的输入会保留。'
              : 'A page resource failed to load, most likely because Argus was just updated. Reload; unsent input is kept.'}
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <button type="button" className="rounded-md bg-blue px-4 py-2 text-sm text-on-accent" onClick={() => window.location.reload()}>
              {zh ? '重新加载工作台' : 'Reload workspace'}
            </button>
            <button type="button" className="rounded-md border border-line px-4 py-2 text-sm" onClick={() => {
              const url = new URL(window.location.href);
              url.searchParams.set('view', 'activity');
              // URL preference is authoritative even when storage is unavailable.
              window.location.assign(url.toString());
            }}>
              {zh ? '返回对话页面' : 'Return to conversation'}
            </button>
          </div>
        </section>
      </main>
    );
  }
}

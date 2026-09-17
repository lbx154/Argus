import { Component, type ReactNode } from 'react';
import type { Locale } from '../i18n';

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
          <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-blue">Argus</p>
          <h1 className="text-lg font-semibold">{zh ? '工作台暂时无法显示' : 'The workspace could not be displayed'}</h1>
          <p className="mt-3 text-sm leading-relaxed text-ink-dim">
            {zh ? '页面资源未能正确加载。后端任务不会因此被停止；你仍可使用桌面菜单查看日志或设置。重新加载会丢弃页面中尚未发送的输入。'
              : 'A page resource failed to load. Backend work has not been stopped. Desktop menus remain available for logs and settings. Reloading discards unsent input on this page.'}
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <button type="button" className="rounded-md bg-blue px-4 py-2 text-sm text-white" onClick={() => window.location.reload()}>
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

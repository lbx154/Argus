/* First-party host adapter, not a modification of the proprietary wheel.
 * Uses the pinned 0.4.0 page's public form controls and standard input events;
 * never accesses React internals, account values, arbitrary files or model APIs. */
(() => {
  'use strict';
  if (window.parent === window || window.__argusDesktopPaths) return;
  window.__argusDesktopPaths = true;
  const parent = window.parent;
  const nativeOrigins = new Set(['http://tauri.localhost', 'https://tauri.localhost', 'tauri://localhost']);
  const handshake = crypto.randomUUID();
  const pending = new Map();
  let origin = null;
  const english = () => document.documentElement.lang.toLowerCase().startsWith('en');
  const decorate = () => {
    if (!origin) return;
    document.querySelectorAll('button[data-testid="browse-folder"]').forEach(button => {
      button.dataset.argusNativePicker = 'ready';
      button.title = english() ? 'Browse folders on this PC' : '使用系统窗口浏览此电脑中的文件夹';
    });
  };
  const listener = event => {
    if (event.source !== parent || !nativeOrigins.has(event.origin)) return;
    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if (data.type === 'argus:path-capabilities' && data.requestId === handshake
      && data.version === 1 && Array.isArray(data.kinds) && data.kinds.includes('folder')) {
      origin = event.origin;
      decorate();
      return;
    }
    if (!origin || event.origin !== origin || data.type !== 'argus:path-result') return;
    const request = pending.get(data.requestId);
    if (!request) return;
    pending.delete(data.requestId);
    clearTimeout(request.timer);
    request.resolve(data);
  };
  window.addEventListener('message', listener);
  const observer = new MutationObserver(decorate);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  decorate();
  parent.postMessage({ type: 'argus:path-capabilities', version: 1, requestId: handshake }, '*');

  document.addEventListener('click', async event => {
    const button = event.target instanceof Element
      ? event.target.closest('button[data-testid="browse-folder"]') : null;
    if (!origin || !button || button.disabled || !event.isTrusted) return;
    const form = button.closest('form');
    const input = form?.querySelector('input:not([type]), input[type="text"]');
    // If a future/control variant differs, leave the plugin's existing browser
    // untouched rather than guessing which field represents the destination.
    if (!(input instanceof HTMLInputElement)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    button.disabled = true;
    const requestId = crypto.randomUUID();
    const result = await new Promise(resolve => {
      const timer = setTimeout(() => {
        pending.delete(requestId);
        resolve({ error: english() ? 'Selection timed out; cancel the system dialog to try again.' : '选择等待超时；请先取消系统窗口，再重试或输入路径。' });
      }, 120000);
      pending.set(requestId, { resolve, timer });
      parent.postMessage({ type: 'argus:choose-path', version: 1, requestId, kind: 'folder' }, origin);
    });
    if (!button.isConnected || !input.isConnected) return;
    button.disabled = false;
    form.querySelector('[data-argus-picker-status]')?.remove();
    if (result.error) {
      const status = document.createElement('p');
      status.dataset.argusPickerStatus = '';
      status.setAttribute('role', 'status');
      status.style.gridColumn = '1 / -1';
      status.textContent = english() ? 'The system folder picker is unavailable. You can still enter a path.' : String(result.error);
      form.append(status);
    } else if (!result.cancelled && typeof result.path === 'string' && result.path.length <= 32767
      && !/[\r\n\0]/.test(result.path)) {
      // Native setter + a real DOM input event updates a controlled form without
      // accessing any framework-private tracker. Do not submit/open/run a task.
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, result.path);
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    input.focus({ preventScroll: true });
  }, true);
  window.addEventListener('pagehide', () => {
    observer.disconnect();
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.resolve({ cancelled: true });
    }
    pending.clear();
  }, { once: true });
})();

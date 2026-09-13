import { desktopBridge } from './bridge';

/** A narrow request/reply bridge. No filesystem reads, arbitrary IPC or paths outside a user dialog. */
export function nativePathRequests(frame: HTMLIFrameElement, origin: () => string | null) {
  let documentVersion = 0;
  let busy = false;
  const reply = (requestId: string, payload: Record<string, unknown>, expectedVersion: number, target: string) => {
    if (expectedVersion !== documentVersion || target !== origin()) return;
    frame.contentWindow?.postMessage({ type: 'argus:path-result', requestId, ...payload }, target);
  };
  return {
    navigated: () => { documentVersion++; },
    receive: (event: MessageEvent): void => {
      const target = origin();
      if (!target || event.origin !== target || event.source !== frame.contentWindow) return;
      const data = event.data;
      if (!data || typeof data !== 'object' || data.version !== 1
        || typeof data.requestId !== 'string' || !/^[a-zA-Z0-9-]{1,80}$/.test(data.requestId)) return;
      if (data.type === 'argus:path-capabilities') {
        frame.contentWindow?.postMessage({ type: 'argus:path-capabilities', requestId: data.requestId, version: 1,
          kinds: ['folder', 'cif'] }, target);
        return;
      }
      if (data.type !== 'argus:choose-path' || !['folder', 'cif'].includes(data.kind)) return;
      const version = documentVersion;
      if (busy) { reply(data.requestId, { error: '已有文件选择窗口，请先完成或取消。' }, version, target); return; }
      busy = true;
      // Do not accept an arbitrary initial path or URL from a remote document:
      // even visiting a supplied network path can trigger Windows authentication.
      void desktopBridge.chooseLocalPath(data.kind).then(path => {
        reply(data.requestId, { path, cancelled: path === null }, version, target);
      }).catch(() => {
        reply(data.requestId, { error: '无法打开系统选择窗口；仍可使用路径输入。' }, version, target);
      }).finally(() => { busy = false; });
    },
  };
}

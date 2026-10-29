import { act, create } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { StoragePauseBanner } from '../components/StoragePauseBanner';
import { I18nProvider } from '../i18n';
import { errorText } from '../lib/format';

afterEach(() => vi.unstubAllGlobals());

const GB = 1024 ** 3;

it.each([
  ['zh-CN', '工作区存储已满（34.0 GB / 30.0 GB），任务已暂停，文件都保留着；清理文件或联系管理员后会继续。'],
  ['en', 'Workspace storage is full (34.0 GB / 30.0 GB). Work is paused and every file is kept'],
])('says the workspace is full, by how much, and that files are kept (%s)', (locale, expected) => {
  vi.stubGlobal('localStorage', { getItem: () => locale });
  vi.stubGlobal('document', { documentElement: { lang: '' } });
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<I18nProvider><StoragePauseBanner pause={{ used_bytes: 34 * GB, limit_bytes: 30 * GB, since: 1 }} /></I18nProvider>); });
  const notice = renderer.root.findByProps({ role: 'alert' });
  expect(notice.children.join('')).toContain(expected);
  act(() => renderer.update(<I18nProvider><StoragePauseBanner pause={null} /></I18nProvider>));
  expect(renderer.toJSON()).toBeNull();
  act(() => renderer.unmount());
});

it('words a refused start the same way instead of the server sentence', () => {
  const text = errorText(new Error('workspace storage is full (34.0 GB of 30.0 GB); work stays paused until files are removed'));
  expect(text).toMatch(/34\.0 GB \/ 30\.0 GB/);
  expect(text).not.toMatch(/work stays paused until/);
});

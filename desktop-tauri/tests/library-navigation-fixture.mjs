import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { startSessionFixture } from './session-context-fixture.mjs';
import { knowledgeFixture } from './knowledge-fixture.mjs';
const root = resolve(import.meta.dirname, '../..');
export const PERSONAL_TEXT = '# SYNTHETIC PERSONAL NOTE\n\nSynthetic original material; not a real operator profile.\n';
export async function startLibraryFixture(pluginState = 'manual') {
  const guides = JSON.parse(await readFile(resolve(root, 'frontend/web/src/lib/bundledSkillChinese.json'), 'utf8'));
  const items = Object.entries(guides).map(([key, guide]) => {
    const [scope, ...parts] = key.split('/');
    const vertical = scope === 'vertical' ? parts.shift() : '';
    return { scope, vertical, library: scope === 'global' ? 'global:bundled' : `${vertical === 'chemistry' ? 'domain' : 'vertical'}:${vertical}:bundled`,
      path: parts.join('/'), name: guide.originalName, description: 'Original bundled metadata',
      source: 'bundled', role: ['manager', 'planner', 'reviewer', 'curator'].includes(parts[0]) ? parts[0] : 'engineer', is_default: true, updated_at: null };
  });
  const learned = { scope: 'project', vertical: '', library: 'project:learned', path: 'engineer/local-method.md',
    name: '项目自定步骤（原始标题）', description: '原始用途说明，不得自动改写。', source: 'project', role: 'engineer', is_default: false, updated_at: 2000 };
  const personal = { scope: 'private', vertical: '', root: 'synthetic/private-notes', path: 'notes/personal.md', title: '合成个人记录',
    description: '原始记录摘要', kind: 'note', source: 'synthetic conversation', created: '2026-09-18', updated_at: 2001, reuse_count: 0 };
  const plugin = { id: 'crystalpilot', name: 'CrystalPilot', description: '合成晶体研究插件，测试不安装或启动。',
    version: '0.4.0', installed_version: '0.4.0', installed: pluginState === 'ready', enabled: pluginState === 'ready',
    supported: true, reason: '', update_available: false, backends: {}, url: '/synthetic-workbench',
    managed_by_host: pluginState !== 'manual', operation: pluginState === 'preparing' ? { status: 'running', progress: '正在准备合成工作台' }
      : pluginState === 'failed' ? { status: 'failed', error: '合成准备失败' } : undefined };
  return startSessionFixture({ read: async ({ url }) => {
    if (url.pathname === '/api/plugins') return { body: { plugins: [plugin] } };
    if (url.pathname === '/api/skill-library') return { body: { scopes: ['global', 'vertical', 'project'], items: [...items, learned],
      verticals: [...new Set(items.map(item => item.vertical).filter(Boolean))], active_vertical: 'research', errors: [] } };
    if (url.pathname === '/api/skill-library/document') {
      const selected = [...items, learned].find(item => item.library === url.searchParams.get('library') && item.path === url.searchParams.get('path'));
      if (!selected) return { status: 404, body: { error: 'Synthetic document not found' } };
      if (selected === learned) return { body: { ...learned, markdown: '# Original custom instructions\n', content: '# Original custom instructions\n\nDo not translate or rewrite this original.\n' } };
      const key = selected.scope === 'global' ? `global/${selected.path}` : `vertical/${selected.vertical}/${selected.path}`;
      // The only file reads are exact, predeclared repository-owned sources.
      const markdown = (await readFile(resolve(root, guides[key].source), 'utf8')).replaceAll('\r\n', '\n');
      return { body: { ...selected, markdown, content: markdown.replace(/^---\n[\s\S]*?\n---\s*\n/, '') } };
    }
    if (url.pathname === '/api/wiki/page' && url.searchParams.get('scope') === 'private') {
      if (url.searchParams.get('path') !== personal.path) return { status: 404, body: { error: 'Synthetic personal note not found' } };
      return { body: { ...personal, content: PERSONAL_TEXT, markdown: PERSONAL_TEXT, truncated: false } };
    }
    if (url.pathname === '/api/wiki') {
      const base = knowledgeFixture(url).body;
      return { body: { ...base, scopes: ['private', ...base.scopes], items: [personal, ...base.items],
        libraries: [{ scope: 'private', vertical: '', root: personal.root, pages: [personal], index_markdown: '# 合成个人资料索引', profile: 'Synthetic profile stays unchanged.' }, ...base.libraries] } };
    }
    return null;
  } });
}

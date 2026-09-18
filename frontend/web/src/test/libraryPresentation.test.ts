import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';
import type { SkillLibraryItem } from '../api';
import guides from '../lib/bundledSkillChinese.json';
import { bundledSkillGuide, knowledgePurpose, knowledgeScopePurpose, libraryVertical, skillPresentation, skillSearchText } from '../lib/libraryPresentation';

const root = resolve(import.meta.dirname, '../../../..');
function row(key: string): SkillLibraryItem {
  const [scope, ...parts] = key.split('/');
  const vertical = scope === 'vertical' ? parts.shift()! : '';
  const guide = guides[key as keyof typeof guides];
  return { scope: scope as 'global' | 'vertical', vertical, path: parts.join('/'),
    library: scope === 'global' ? 'global:bundled' : `${vertical === 'chemistry' ? 'domain' : 'vertical'}:${vertical}:bundled`,
    name: guide.originalName, description: 'Original metadata', source: 'bundled', is_default: true, updated_at: null, role: 'engineer' };
}
describe('source-bound Chinese display guides (not runtime instructions)', () => {
  it('covers every shipped catalog Markdown source, including domain/reference pages', () => {
    const sources = execFileSync('git', ['ls-files', '--', 'argus/builtin_skills', 'argus/domains', 'argus/verticals'], { cwd: root, encoding: 'utf8' })
      .trim().split('\n').filter(path => path.endsWith('.md') && (path.startsWith('argus/builtin_skills/') || path.includes('/skills/')));
    expect(Object.values(guides).map(guide => guide.source).sort()).toEqual(sources.sort());
  });
  it.each(Object.entries(guides))('keeps %s bound to its unmodified source and supplies a Chinese purpose', (key, guide) => {
    expect(guide.source).toMatch(/^argus\/(builtin_skills|domains|verticals)\//);
    expect(guide.source).not.toMatch(/\.\.\//);
    expect(createHash('sha256').update(readFileSync(resolve(root, guide.source))).digest('hex')).toBe(guide.sourceSha256);
    expect(guide.title).toMatch(/[\u3400-\u9fff]/);
    expect(guide.summary).toMatch(/[\u3400-\u9fff]/);
    const item = row(key);
    expect(bundledSkillGuide(item)).toEqual(guide);
    expect(skillPresentation(item, 'zh-CN').title).toBe(guide.title);
    expect(skillPresentation(item, 'en').title).toBe(item.name);
    expect(skillSearchText(item, 'zh-CN')).toContain(guide.title.toLowerCase());
  });
  it('never relabels edited/learned/custom/third-party content because a filename matches', () => {
    const item = row('global/engineer/pdf-chat.md');
    for (const changed of [{ is_default: false }, { source: 'native' as const }, { library: 'project:native' },
      { name: 'My own reading instructions' }, { scope: 'project' as const }]) {
      const custom = { ...item, ...changed };
      expect(bundledSkillGuide(custom)).toBeNull();
      expect(skillPresentation(custom, 'zh-CN').title).toBe(custom.name);
    }
    expect(bundledSkillGuide({ ...item, source: 'shared', library: 'global:shared' })?.title).toBe('分段阅读 PDF');
  });
  it('does not turn unknown domain identifiers or knowledge kinds into invented facts', () => {
    expect(libraryVertical('research', 'zh-CN')).toBe('科研');
    expect(libraryVertical('new-domain', 'zh-CN')).toBe('new-domain');
    expect(knowledgePurpose('unknown', 'zh-CN')).toContain('一般参考资料');
    expect(knowledgePurpose('lesson', 'zh-CN')).toContain('不能代替当前证据');
    expect(knowledgeScopePurpose('private', 'zh-CN')).toContain('不改变既有读取权限');
  });
});

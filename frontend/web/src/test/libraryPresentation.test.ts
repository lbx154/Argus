import { expect, it } from 'vitest';
import type { SkillLibraryItem } from '../api';
import { libraryVertical, skillPresentation, skillSearchText } from '../lib/libraryPresentation';

const skill = { name: 'software', description: 'A change that builds', path: 'software/SKILL.md',
  scope: 'global', library: 'bundled', source: 'bundled', is_default: true, vertical: '' } as SkillLibraryItem;

it('shows actual skill metadata and searches the original description in either language', () => {
  expect(skillPresentation(skill, 'zh-CN')).toEqual({ title: 'software', summary: '' });
  expect(skillPresentation(skill, 'en')).toEqual({ title: 'software', summary: skill.description });
  expect(skillSearchText(skill, 'zh-CN')).toContain(skill.description.toLowerCase());
  const learned = { ...skill, name: '团队方法', description: '真实的中文描述', is_default: false, source: 'shared' } as SkillLibraryItem;
  expect(skillPresentation(learned, 'zh-CN')).toEqual({ title: learned.name, summary: learned.description });
});

it('keeps unknown domain identifiers as recorded', () => {
  expect(libraryVertical('research', 'zh-CN')).toBe('科研');
  expect(libraryVertical('new-domain', 'zh-CN')).toBe('new-domain');
});

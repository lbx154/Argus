import { expect, it } from 'vitest';
import type { SkillLibraryItem } from '../api';
import { skillPresentation, skillSearchText } from '../lib/libraryPresentation';

const skill = { name: 'software', description: 'A change that builds', path: 'software/SKILL.md',
  library: 'bundled', source: 'bundled' } as SkillLibraryItem;

it('shows actual skill metadata and searches the original description in either language', () => {
  expect(skillPresentation(skill)).toEqual({ title: 'software', summary: skill.description });
  expect(skillSearchText(skill)).toContain(skill.description.toLowerCase());
  const learned = { ...skill, name: '团队方法', description: '真实的中文描述', source: 'shared' } as SkillLibraryItem;
  expect(skillPresentation(learned)).toEqual({ title: learned.name, summary: learned.description });
});

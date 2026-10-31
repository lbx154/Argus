import type { SkillLibraryItem } from '../api';
export function skillPresentation(item: SkillLibraryItem) {
  return { title: item.name, summary: item.description };
}
export function skillSearchText(item: SkillLibraryItem): string {
  return `${item.name} ${item.description} ${item.path}`.toLocaleLowerCase();
}

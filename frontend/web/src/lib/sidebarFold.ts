import { useCallback, useState } from 'react';
import { readLocalStorage, writeLocalStorage } from './storage';

/** A sidebar section the operator can fold; the choice is remembered per section. Folded by default. */
export function useSidebarFold(section: string, defaultExpanded = false): [boolean, () => void] {
  const key = `argus.sidebar.${section}.expanded`;
  const [expanded, setExpanded] = useState<boolean>(() => {
    const stored = readLocalStorage(key);
    return stored === null ? defaultExpanded : stored === '1';
  });
  const toggle = useCallback(() => {
    setExpanded(value => { writeLocalStorage(key, value ? '0' : '1'); return !value; });
  }, [key]);
  return [expanded, toggle];
}

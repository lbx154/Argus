import { useEffect } from 'react';

interface UseGlobalKeyboardShortcutsOptions {
  focusComposer: () => void;
  openHelp: () => void;
  toggleKiosk: () => void;
  togglePalette: () => void;
  toggleReasoning: () => void;
  toggleSidebarCollapse: () => void;
}

export function useGlobalKeyboardShortcuts({
  focusComposer,
  openHelp,
  toggleKiosk,
  togglePalette,
  toggleReasoning,
  toggleSidebarCollapse,
}: UseGlobalKeyboardShortcutsOptions) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target as HTMLElement | null;
      const typing = el?.tagName === 'INPUT' || el?.tagName === 'TEXTAREA';
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        togglePalette();
      } else if (mod && event.key.toLowerCase() === 't') {
        event.preventDefault();
        toggleReasoning();
      } else if (mod && event.key === '.') {
        event.preventDefault();
        toggleKiosk();
      } else if (mod && event.key.toLowerCase() === 'b') {
        event.preventDefault();
        toggleSidebarCollapse();
      } else if (mod && event.key.toLowerCase() === 'j') {
        event.preventDefault();
        focusComposer();
      } else if (!typing && event.key === '?') {
        event.preventDefault();
        openHelp();
      } else if (!typing && event.key === '/') {
        event.preventDefault();
        focusComposer();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [focusComposer, openHelp, toggleKiosk, togglePalette, toggleReasoning, toggleSidebarCollapse]);
}

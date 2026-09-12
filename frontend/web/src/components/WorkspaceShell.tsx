import { forwardRef, type HTMLAttributes } from 'react';

/** Shared workbench chrome; each surface owns its data and actions. */
export const WorkspaceShell = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  function WorkspaceShell({ className = '', ...props }, ref) {
    return <div ref={ref} className={`workbench-shell ambient-canvas flex w-screen max-w-full overflow-hidden text-ink ${className}`} {...props} />;
  },
);

export function WorkspaceHeader({ className = '', ...props }: HTMLAttributes<HTMLElement>) {
  return <header className={`chrome-seam-surface glass-panel glass-panel--raised flex h-12 min-w-0 shrink-0 items-center gap-2 border-b px-3 sm:gap-3 sm:px-4 ${className}`} {...props} />;
}

interface SidePanelProps extends HTMLAttributes<HTMLElement> {
  mobileOpen?: boolean;
  collapsed?: boolean;
}

export function WorkspaceSidePanel({ mobileOpen = false, collapsed = false, className = '', ...props }: SidePanelProps) {
  const slim = collapsed && !mobileOpen;
  return (
    <aside
      data-state={slim ? 'collapsed' : 'expanded'}
      data-resizable-panel="left"
      className={`glass-panel glass-panel--side fixed inset-y-0 left-0 z-50 flex h-full shrink-0 flex-col border-r transition-[width,transform,visibility] duration-panel ease-panel lg:visible lg:static lg:z-auto lg:translate-x-0 ${slim ? 'w-14' : 'w-64 lg:w-[var(--sidebar-width,256px)]'} ${mobileOpen ? 'visible translate-x-0' : 'invisible -translate-x-full'} ${className}`}
      {...props}
    />
  );
}

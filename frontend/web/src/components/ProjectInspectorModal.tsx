import type { JournalEntry, Snapshot } from '../api';
import { BacklogPanel } from './BacklogPanel';
import { JournalPanel } from './JournalPanel';
import { Modal, ModalHeader } from './Modal';
import { RolesPanel } from './RolesPanel';

export function ProjectInspectorModal({
  open,
  snap,
  journal,
  busy,
  onClose,
  onDispose,
  onStop,
  onInspect,
}: {
  open: boolean;
  snap: Snapshot;
  journal: JournalEntry[];
  busy: boolean;
  onClose: () => void;
  onDispose: (id: string, op: 'done' | 'skip' | 'rm') => void;
  onStop: (id: string) => void;
  onInspect: (id: string) => void;
}) {
  return (
    <Modal open={open} onClose={onClose} label="Project inspector" width="max-w-6xl">
      <ModalHeader title="Project" sub={snap.session.display_name || snap.session.id} />
      <div className="h-[68vh] min-h-0 space-y-3 overflow-y-auto bg-bg p-3 scroll-thin lg:grid lg:grid-cols-[minmax(0,1.4fr)_minmax(300px,0.8fr)] lg:gap-3 lg:space-y-0 lg:overflow-hidden">
        <BacklogPanel
          items={snap.backlog}
          onDispose={onDispose}
          onStop={onStop}
          onInspect={onInspect}
          busy={busy}
        />
        <div className="flex min-h-0 flex-col gap-3">
          <RolesPanel roles={snap.roles} />
          <JournalPanel entries={journal} />
        </div>
      </div>
    </Modal>
  );
}

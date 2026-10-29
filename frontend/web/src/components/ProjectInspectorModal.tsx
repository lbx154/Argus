import type { Snapshot } from '../api';
import { useI18n } from '../i18n';
import { BacklogPanel } from './BacklogPanel';
import { Modal, ModalHeader } from './Modal';
import { RolesPanel } from './RolesPanel';

export function ProjectInspectorModal({
  open,
  snap,
  onClose,
  onInspect,
}: {
  open: boolean;
  snap: Snapshot;
  onClose: () => void;
  onInspect: (id: string) => void;
}) {
  const { t } = useI18n();
  return (
    <Modal open={open} onClose={onClose} label={t('panel.project')} width="max-w-6xl">
      <ModalHeader title={t('panel.project')} sub={snap.session.display_name || snap.session.id} />
      <div className="h-[68vh] min-h-0 space-y-3 overflow-y-auto bg-bg p-3 scroll-thin lg:grid lg:grid-cols-[minmax(0,1.4fr)_minmax(300px,0.8fr)] lg:gap-3 lg:space-y-0 lg:overflow-hidden">
        <BacklogPanel items={snap.backlog} onInspect={onInspect} />
        <RolesPanel roles={snap.roles} />
      </div>
    </Modal>
  );
}

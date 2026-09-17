import type { ArtifactInfo, DeliveryReceipt, EventMsg } from '../../../core/src/types';
import { EventStream } from '../components/EventStream';
import { X, MessageCircle } from 'lucide-react';

export function MapConversation({ events, connected, pending, artifacts, zh, onClose, onOpenArtifact, onOpenDelivery }: {
  events: EventMsg[]; connected: boolean; pending: boolean; artifacts: ArtifactInfo[]; zh: boolean;
  onClose: () => void; onOpenArtifact: (path: string) => void; onOpenDelivery: (receipt: DeliveryReceipt) => void;
}) {
  const conversation = events.filter((event) => event.type === 'ui.operator' || event.type === 'ui.argus');
  return <aside className="map-conversation nowheel nodrag nopan" aria-label={zh ? '地图对话' : 'Map conversation'}>
    <header><MessageCircle size={16} /><strong>{zh ? '与 Argus 对话' : 'Talk to Argus'}</strong><button type="button" onClick={onClose} aria-label={zh ? '关闭对话' : 'Close conversation'}><X size={18} /></button></header>
    {conversation.length ? <EventStream events={conversation} connected={connected} showReasoning={false} onToggleReasoning={() => {}} embedded showHeader={false} artifacts={artifacts} onOpenArtifact={onOpenArtifact} onOpenDelivery={onOpenDelivery} /> : <p className="map-conversation-empty">{zh ? '在下方发送目标或问题，回复会保留在这里。' : 'Send a goal or question below. Your conversation stays here.'}</p>}
    {pending && <p className="map-conversation-pending" role="status">{zh ? 'Argus 正在回复…' : 'Argus is replying…'}</p>}
  </aside>;
}

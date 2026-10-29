import type { ArtifactInfo, DeliveryReceipt, EventMsg } from '../../../core/src/types';
import { EventStream } from '../components/EventStream';
import { X, MessageCircle } from 'lucide-react';
import { applicationBrand } from '../applicationBrand';
import type { ConversationPagination } from '../useConversationHistory';

export function MapConversation({ events, connected, pending, artifacts, zh, pagination, onClose, onOpenArtifact, onOpenDelivery }: {
  events: EventMsg[]; connected: boolean; pending: boolean; artifacts: ArtifactInfo[]; zh: boolean;
  pagination?: ConversationPagination;
  onClose: () => void; onOpenArtifact: (path: string) => void; onOpenDelivery: (receipt: DeliveryReceipt) => void;
}) {
  const conversation = events;
  return <aside className="map-conversation nowheel nodrag nopan" aria-label={zh ? '地图对话' : 'Map conversation'}>
    <header><MessageCircle size={16} /><strong>{zh ? `与 ${applicationBrand().name} 对话` : `Talk to ${applicationBrand().name}`}</strong><button type="button" onClick={onClose} aria-label={zh ? '关闭对话' : 'Close conversation'}><X size={18} /></button></header>
    {conversation.length ? <EventStream events={conversation} {...pagination} connected={connected} showReasoning={false} embedded showHeader={false} artifacts={artifacts} onOpenArtifact={onOpenArtifact} onOpenDelivery={onOpenDelivery} /> : <p className="map-conversation-empty">{zh ? '在下方发送目标或问题，回复会保留在这里。' : 'Send a goal or question below. Your conversation stays here.'}</p>}
    {pending && !conversation.some(event => event.type === 'ui.argus' && event.live === true) && <p className="map-conversation-pending" role="status">{zh ? `${applicationBrand().name} 正在回复…` : `${applicationBrand().name} is replying…`}</p>}
  </aside>;
}

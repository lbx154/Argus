import type { ArtifactInfo, DeliveryReceipt, EventMsg } from '../../../core/src/types';
import { deliveryFiles } from '../components/deliveryPresentation';
import { eventTaskId } from '../lib/workStatus';

const IMAGE_FILE = /\.(?:png|jpe?g|gif|webp|svg)$/i;
const IMAGE_PATH = /[\w./~@+-]+\.(?:png|jpe?g|gif|webp|svg)\b/gi;

/**
 * The figure each task's map card shows: the first picture its newest
 * delivery names, else the newest picture its own work names (made it, opened
 * it) that the project already exposes. Only exposed files can be loaded, so
 * a path never becomes a broken image; `workspace` turns the absolute paths
 * the agents write into the workspace paths the artifact list uses.
 */
export function taskFigures(
  deliveries: DeliveryReceipt[],
  artifacts: ArtifactInfo[],
  events: EventMsg[],
  workspace: string,
): Map<string, string> {
  const figures = new Map<string, string>();
  const delivered = [...deliveries].sort((a, b) => b.delivered_at - a.delivered_at);
  for (const receipt of delivered) {
    if (!receipt.item_id || figures.has(receipt.item_id)) continue;
    const image = deliveryFiles(receipt).find((file) => IMAGE_FILE.test(file.path));
    if (image) figures.set(receipt.item_id, image.path);
  }
  const exposed = new Set(artifacts.filter((file) => file.exists && (file.kind === 'image' || IMAGE_FILE.test(file.path)))
    .map((file) => file.path));
  if (!exposed.size) return figures;
  const root = workspace.replace(/\/+$/, '');
  const relative = (path: string) => (root && path.startsWith(`${root}/`) ? path.slice(root.length + 1) : path).replace(/^\.\//, '');
  const named = new Map<string, Set<string>>();
  for (const event of events) {
    const taskId = eventTaskId(event);
    if (!taskId || figures.has(taskId)) continue;
    const text = `${String(event.text ?? '')}\n${String(event.detail ?? '')}`;
    for (const match of text.matchAll(IMAGE_PATH)) {
      const path = relative(match[0]);
      if (exposed.has(path)) named.set(taskId, (named.get(taskId) ?? new Set()).add(path));
    }
  }
  // The project lists its figures in the order it declared them, the main one
  // first; that order, not which one the work opened last, picks the figure.
  for (const [taskId, paths] of named) {
    const primary = [...exposed].find((path) => paths.has(path));
    if (primary) figures.set(taskId, primary);
  }
  return figures;
}

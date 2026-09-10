export const PREVIEW_MIN_WIDTH = 320;
export const PREVIEW_MAX_WIDTH = 840;
export const PREVIEW_DEFAULT_WIDTH = 440;

/** Give an explicitly opened file ~45% of the window, retaining room to chat. */
export function preferredPreviewWidth(shellWidth: number, leftWidth: number, leftOpen: boolean): number {
  const occupiedLeft = leftOpen ? leftWidth + 8 : 56;
  const available = Math.max(PREVIEW_MIN_WIDTH, shellWidth - occupiedLeft - 360 - 8);
  return Math.max(PREVIEW_MIN_WIDTH, Math.min(PREVIEW_MAX_WIDTH, available, Math.round(shellWidth * 0.45)));
}

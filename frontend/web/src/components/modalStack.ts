const focusableSelector = 'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), summary, [href], [tabindex]:not([tabindex="-1"])';

type ModalLayer = {
  dialog: HTMLElement;
  close: () => void;
  previous: HTMLElement | null;
};

const layers: ModalLayer[] = [];
const topLayer = () => layers[layers.length - 1];
const focusableElements = (dialog: HTMLElement) => Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector))
  .filter(element => element.getAttribute('aria-hidden') !== 'true'
    && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden');

function focusDialog(dialog: HTMLElement) {
  const target = dialog.querySelector<HTMLElement>('[data-autofocus]')
    ?? dialog.querySelector<HTMLElement>(focusableSelector);
  (target ?? dialog).focus();
}

function onKey(event: KeyboardEvent) {
  // One listener owns the event even when closing synchronously exposes another layer.
  const layer = topLayer();
  if (!layer) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    layer.close();
    return;
  }
  if (event.key !== 'Tab') return;
  const focusable = focusableElements(layer.dialog);
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (!first) {
    event.preventDefault();
    layer.dialog.focus();
  } else if (!layer.dialog.contains(document.activeElement)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

export function registerModal(dialog: HTMLElement, close: () => void) {
  const layer: ModalLayer = {
    dialog, close,
    previous: document.activeElement instanceof HTMLElement ? document.activeElement : null,
  };
  // Shared wrappers all use z-50: DOM order, including nested children, owns
  // their visual order even when an earlier sibling opens later.
  const visualIndex = layers.findIndex(candidate => {
    const position = dialog.compareDocumentPosition(candidate.dialog);
    return !(position & Node.DOCUMENT_POSITION_DISCONNECTED) && !!(position & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  layers.splice(visualIndex < 0 ? layers.length : visualIndex, 0, layer);
  if (layers.length === 1) window.addEventListener('keydown', onKey);
  const frame = window.requestAnimationFrame(() => {
    if (topLayer() === layer) focusDialog(dialog);
  });

  return () => {
    window.cancelAnimationFrame(frame);
    const index = layers.indexOf(layer);
    if (index < 0) return;
    const wasTop = topLayer() === layer;
    layers.splice(index, 1);
    // Keep the restoration chain valid if a lower dialog disappears first.
    for (const remaining of layers) {
      if (remaining.previous && dialog.contains(remaining.previous)) remaining.previous = layer.previous;
    }
    if (!layers.length) window.removeEventListener('keydown', onKey);
    const active = document.activeElement;
    const mayRestore = !active || active === document.body || !active.isConnected || dialog.contains(active);
    if (!wasTop || !mayRestore) return;
    const remaining = topLayer();
    if (layer.previous?.isConnected && (!remaining || remaining.dialog.contains(layer.previous))) {
      layer.previous.focus();
    } else if (remaining) {
      focusDialog(remaining.dialog);
    }
  };
}

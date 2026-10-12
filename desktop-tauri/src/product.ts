interface DesktopBrand {
  name: string;
  mark: string;
  markDark: string;
}

declare const __ARGUS_DESKTOP_BRAND__: DesktopBrand | null;

const brand = __ARGUS_DESKTOP_BRAND__;
export const applicationName = brand?.name ?? 'Argus';
export const hasApplicationBrand = brand !== null;

if (brand) {
  for (const element of document.querySelectorAll('[data-desktop-brand]')) {
    for (const child of element.childNodes) {
      if (child.nodeType === Node.TEXT_NODE && child.textContent) {
        child.textContent = child.textContent.replaceAll('Argus', brand.name);
      }
    }
    for (const attribute of ['title', 'aria-label']) {
      const value = element.getAttribute(attribute);
      if (value) element.setAttribute(attribute, value.replaceAll('Argus', brand.name));
    }
  }
  for (const glyph of document.querySelectorAll<SVGElement>(
    '.window-titlebar-mark, .argus-splash-mark, .wizard-mark-glyph',
  )) {
    for (const theme of ['light', 'dark'] as const) {
      const image = document.createElement('img');
      image.src = theme === 'dark' ? brand.markDark : brand.mark;
      image.alt = '';
      image.className = `${glyph.getAttribute('class')} desktop-product-${theme}`;
      glyph.after(image);
    }
    glyph.style.display = 'none';
  }
}

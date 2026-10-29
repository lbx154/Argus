export type ApplicationBrand = {
  schema: 1;
  id: string;
  name: string;
  tagline: string;
  terminal_wordmark: string;
};

export const DEFAULT_APPLICATION_BRAND: ApplicationBrand = {
  schema: 1, id: 'argus', name: 'Argus', tagline: 'Autonomous Work Lab',
  terminal_wordmark: 'argus',
};

export function parseApplicationBrand(value: unknown): ApplicationBrand {
  if (!value || typeof value !== 'object') throw new Error('Invalid application brand');
  const data = value as Record<string, unknown>;
  if (data.schema !== 1 || typeof data.id !== 'string' || !/^[a-z][a-z0-9-]{0,39}$/.test(data.id)) {
    throw new Error('Invalid application brand identity');
  }
  for (const [key, limit] of [['name', 48], ['tagline', 100], ['terminal_wordmark', 24]] as const) {
    const text = data[key];
    if (typeof text !== 'string' || !text.trim() || text.length > limit || /[\x00-\x1f\x7f-\x9f]/.test(text)) {
      throw new Error(`Invalid application brand ${key}`);
    }
  }
  if (!/^[a-z0-9 -]+$/.test(String(data.terminal_wordmark))) throw new Error('Invalid terminal wordmark');
  return {
    schema: 1, id: data.id, name: String(data.name), tagline: String(data.tagline),
    terminal_wordmark: String(data.terminal_wordmark),
  };
}

import type { ConfigSnapshot } from '../api';

/** A backend by the name the server gives it, which also lists every backend there is. */
export const backendLabel = (backend: string, config?: ConfigSnapshot): string => config?.backends?.[backend] ?? backend;

/** The project's backend: every role runs on it. */
export const configuredBackend = (config?: ConfigSnapshot): string => config?.roles[0]?.backend ?? '';

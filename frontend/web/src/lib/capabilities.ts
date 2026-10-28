import { createContext, useContext } from 'react';

// Entries the page offers only where the server declares them in GET /api/meta: the Python
// server declares every capability, the TypeScript core those it has.
/** Project settings, and the model chooser that writes them. */
export const SETTINGS_CAPABILITY = 'project.config.v1';
/** The advisor model's settings. */
export const ADVISOR_CAPABILITY = 'advisor.config.v1';
/** The machine's CPU, memory and GPUs. */
export const RESOURCES_CAPABILITY = 'system.resources.v1';
/** The composer's slash commands, /doctor and /config among them. */
export const COMMANDS_CAPABILITY = 'composer.commands.v1';

/** What the server declared; main.tsx provides it once the handshake is in. */
export const Capabilities = createContext<readonly string[] | null>(null);

/** Whether the server has `capability`. A component rendered without a handshake (alone, in a test) offers everything. */
export const useCapability = (capability: string): boolean => useContext(Capabilities)?.includes(capability) ?? true;

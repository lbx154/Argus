import { createContext, useContext } from 'react';

// Entries the page offers only where the server declares them in GET /api/meta: the Python
// server declares every capability, the TypeScript core those it has.
/** Project settings, and the model chooser that writes them. */
export const SETTINGS_CAPABILITY = 'project.config.v1';
/** The machine's CPU, memory and GPUs. */
export const RESOURCES_CAPABILITY = 'system.resources.v1';
/** What runs in a project's folder, read from the machine. */
export const EXPERIMENTS_CAPABILITY = 'system.experiments.v1';
/** The composer's choice of Auto, Task or Chat for a message. */
export const ROUTE_CAPABILITY = 'composer.route.v1';
/** Versions of a result the operator edits, and their history. */
export const EDITIONS_CAPABILITY = 'project.editions.v1';
/** Project state the page asks for again every few seconds: the snapshot, the project list, costs
 * and learning. Without it the server pushes each change (GET /api/projects/stream). */
export const POLL_CAPABILITY = 'project.state-poll.v1';

/** What the server declared; main.tsx provides it once the handshake is in. */
export const Capabilities = createContext<readonly string[] | null>(null);

/** Whether the server has `capability`. A component rendered without a handshake (alone, in a test) offers everything. */
export const useCapability = (capability: string): boolean => useContext(Capabilities)?.includes(capability) ?? true;

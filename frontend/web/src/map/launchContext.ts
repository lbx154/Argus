import { createContext } from "react";

/** The task a held plan waits on and the chat's "开始执行" for it; context so
 * a hold appearing or clearing never rebuilds every card's data. */
export const MapLaunchContext = createContext<{ itemId?: string; start?: () => Promise<void> }>({});

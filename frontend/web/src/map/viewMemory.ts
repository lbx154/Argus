import { readLocalStorage, writeLocalStorage } from "../lib/storage";
import type { layoutScene } from "./submap";
import type { CameraMemory } from "./useSemanticCamera";

const scenes = new Map<string, { scene?: ReturnType<typeof layoutScene>; camera?: CameraMemory }>();
export function recalledView(key: string) {
  const memory = scenes.get(key);
  if (memory) return memory;
  try {
    const camera = JSON.parse(readLocalStorage("argus.map.camera.v1:" + key) || "null");
    if (camera && [camera.viewport, camera.overview].every((v) => v &&
      [v.x, v.y, v.zoom].every(Number.isFinite) && v.zoom >= 0.035 && v.zoom <= 3.5) &&
      (camera.focusId === null || typeof camera.focusId === "string") && typeof camera.detailed === "boolean")
      return { camera: camera as CameraMemory };
  } catch { /* An unavailable view preference never blocks the map. */ }
  return {};
}

export function rememberView(key: string, value: ReturnType<typeof recalledView>) {
  scenes.delete(key);
  scenes.set(key, value);
  while (scenes.size > 8) scenes.delete(scenes.keys().next().value!);
  if (value.camera) writeLocalStorage("argus.map.camera.v1:" + key, JSON.stringify(value.camera));
}

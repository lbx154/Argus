/**
 * Build a three.js object for a robot or mesh result. Every byte comes through
 * the authenticated artifact API first; the loaders then parse buffers in
 * memory and never fetch on their own (a hosted page may only connect to its
 * own origin, with the session's credentials).
 */
import {
  BufferGeometry, Color, LoadingManager, Mesh, MeshStandardMaterial, Object3D, SRGBColorSpace, type Material, type Texture,
} from 'three';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { ColladaLoader } from 'three/examples/jsm/loaders/ColladaLoader.js';
import URDFLoader, { type URDFRobot } from 'urdf-loader';
import { api } from '../../api';
import {
  colladaImageUris, gltfExternalUris, joinModelRef, modelFormat, packGlb, parseUrdf, ModelParseError,
  type ModelFormat, type UrdfSummary,
} from '../../lib/modelFiles';

export interface ModelFailure { ref: string; reason: string }

export interface LoadedModel {
  format: ModelFormat;
  root: Object3D;
  robot: URDFRobot | null;
  summary: UrdfSummary | null;
  failures: ModelFailure[];
  /** Mesh count actually shown, so an all-missing robot is an error, not an empty stage. */
  shapes: number;
}

type Fetcher = (ref: string) => Promise<ArrayBuffer>;

const text = (buffer: ArrayBuffer) => new TextDecoder().decode(buffer);

export const MISSING_FILE = 'file not found next to the model';

function reason(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return /404|not found|unavailable/i.test(message) ? MISSING_FILE : message.slice(0, 200);
}

/** Fetch every reference once; failures are recorded, never thrown. */
async function prefetch(refs: string[], fetchRef: Fetcher, files: Map<string, ArrayBuffer>, failures: ModelFailure[]) {
  await Promise.all(refs.filter((ref) => !files.has(ref)).map(async (ref) => {
    try {
      files.set(ref, await fetchRef(ref));
    } catch (error) {
      if ((error as Error)?.name === 'AbortError') throw error;
      failures.push({ ref, reason: reason(error) });
    }
  }));
}

/** Second-level references: glTF buffers/images and COLLADA textures. */
async function prefetchNested(refs: string[], fetchRef: Fetcher, files: Map<string, ArrayBuffer>, failures: ModelFailure[]) {
  const nested: string[] = [];
  for (const ref of refs) {
    const buffer = files.get(ref);
    if (!buffer) continue;
    const format = modelFormat(ref);
    try {
      if (format === 'gltf') nested.push(...gltfExternalUris(JSON.parse(text(buffer))).map((uri) => joinModelRef(ref, uri)));
      if (format === 'dae') nested.push(...colladaImageUris(text(buffer)).map((uri) => joinModelRef(ref, uri)));
    } catch {
      // The parse step below reports the broken file itself.
    }
  }
  await prefetch(nested, fetchRef, files, failures);
}

/** Textures come from object URLs of prefetched bytes (images may use blob: URLs). */
function imageManager(files: Map<string, ArrayBuffer>, urls: string[], resolve = (url: string) => url) {
  const manager = new LoadingManager();
  const cache = new Map<string, string>();
  manager.setURLModifier((url) => {
    if (url.startsWith('data:') || url.startsWith('blob:')) return url;
    const key = resolve(url);
    if (!cache.has(key)) {
      const bytes = files.get(key);
      const objectUrl = bytes ? URL.createObjectURL(new Blob([bytes])) : 'data:,';
      if (bytes) urls.push(objectUrl);
      cache.set(key, objectUrl);
    }
    return cache.get(key)!;
  });
  return manager;
}

function asMesh(geometry: BufferGeometry, material?: Material) {
  geometry.computeVertexNormals();
  const hasColors = Boolean(geometry.getAttribute('color'));
  return new Mesh(geometry, material ?? new MeshStandardMaterial({ color: 0xb9bec6, roughness: 0.55, metalness: 0.15, vertexColors: hasColors }));
}

async function parseMesh(ref: string, files: Map<string, ArrayBuffer>, urls: string[], material?: Material): Promise<Object3D> {
  const buffer = files.get(ref);
  if (!buffer) throw new ModelParseError(MISSING_FILE);
  const format = modelFormat(ref);
  switch (format) {
    case 'stl':
      return asMesh(new STLLoader().parse(buffer), material);
    case 'obj': {
      const group = new OBJLoader().parse(text(buffer));
      if (material) group.traverse((child) => { if ((child as Mesh).isMesh) (child as Mesh).material = material; });
      return group;
    }
    case 'dae': {
      const manager = imageManager(files, urls, (url) => joinModelRef(ref, url));
      const result = new ColladaLoader(manager).parse(text(buffer), '');
      if (!result?.scene) throw new ModelParseError('COLLADA file has no scene');
      return result.scene;
    }
    case 'glb':
    case 'gltf': {
      let data = buffer;
      if (format === 'gltf') {
        const json = JSON.parse(text(buffer));
        const nested = new Map<string, ArrayBuffer>();
        for (const uri of gltfExternalUris(json)) {
          const bytes = files.get(joinModelRef(ref, uri));
          if (bytes) nested.set(uri, bytes);
        }
        data = packGlb(json, nested);
      }
      const gltf = await new GLTFLoader().parseAsync(data, '');
      return gltf.scene;
    }
    default:
      throw new ModelParseError(`unsupported mesh format: ${ref.split('/').pop()}`);
  }
}

function countShapes(root: Object3D) {
  let shapes = 0;
  root.traverse((child) => { if ((child as Mesh).isMesh) shapes += 1; });
  return shapes;
}

/** URDF materials are Phong; the stage looks softer with physically based ones. */
function soften(root: Object3D) {
  const converted = new Map<Material, Material>();
  root.traverse((child) => {
    const mesh = child as Mesh;
    if (!mesh.isMesh) return;
    const swap = (material: Material) => {
      const phong = material as Material & { isMeshPhongMaterial?: boolean; color?: Color; map?: Texture | null };
      if (!phong.isMeshPhongMaterial) return material;
      if (!converted.has(material)) {
        const standard = new MeshStandardMaterial({ roughness: 0.5, metalness: 0.12 });
        if (phong.color) standard.color.copy(phong.color);
        if (phong.map) { standard.map = phong.map; standard.map.colorSpace = SRGBColorSpace; }
        standard.opacity = material.opacity;
        standard.transparent = material.transparent;
        standard.depthWrite = material.depthWrite;
        standard.name = material.name;
        converted.set(material, standard);
      }
      return converted.get(material)!;
    };
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map(swap) : swap(mesh.material);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
  });
}

export async function loadModel(sid: string, path: string, signal?: AbortSignal): Promise<LoadedModel & { dispose(): void }> {
  const format = modelFormat(path);
  if (!format) throw new ModelParseError('Not a model file');
  const files = new Map<string, ArrayBuffer>();
  const failures: ModelFailure[] = [];
  const urls: string[] = [];
  const fetchRef: Fetcher = async (ref) => (await api.artifactModelAsset(sid, path, ref, signal)).arrayBuffer();
  const source = await (await api.artifactBlob(sid, path, false, signal)).arrayBuffer();
  const dispose = () => urls.forEach((url) => URL.revokeObjectURL(url));

  if (format !== 'urdf') {
    const name = path.split('/').pop() || path;
    files.set(name, source);
    await prefetchNested([name], fetchRef, files, failures);
    const root = await parseMesh(name, files, urls);
    soften(root);
    return { format, root, robot: null, summary: null, failures, shapes: countShapes(root), dispose };
  }

  const summary = parseUrdf(text(source));
  await prefetch([...summary.meshRefs, ...summary.textureRefs], fetchRef, files, failures);
  await prefetchNested(summary.meshRefs, fetchRef, files, failures);
  const pending: Promise<void>[] = [];
  const loader = new URDFLoader(imageManager(files, urls));
  loader.parseVisual = true;
  loader.parseCollision = summary.hasCollision;
  // Keep references exactly as written: they are what the asset route resolves.
  loader.packages = (pkg: string) => `package://${pkg}`;
  loader.loadMeshCb = (ref, _manager, material, done) => {
    if (failures.some((failure) => failure.ref === ref)) { done(null as never); return; }
    pending.push(parseMesh(ref, files, urls, material).then(
      (object) => done(object),
      (error) => { failures.push({ ref, reason: reason(error) }); done(null as never); },
    ));
  };
  const robot = loader.parse(text(source));
  // Meshes arrive asynchronously; wait until every one has been placed or failed.
  for (let settled = 0; settled < pending.length;) {
    const batch = pending.slice(settled);
    settled = pending.length;
    await Promise.all(batch);
  }
  soften(robot);
  return { format, root: robot, robot, summary, failures, shapes: countShapes(robot), dispose };
}

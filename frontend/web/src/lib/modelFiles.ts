/**
 * Robot and 3D model results: which files open in the 3D viewer, what a URDF
 * declares (links, joints and their limits, the meshes it needs), and how a
 * joint maps onto a slider. Pure functions; the three.js side lives in the
 * lazily loaded viewer chunk.
 */

export type ModelFormat = 'urdf' | 'stl' | 'obj' | 'dae' | 'glb' | 'gltf';

export const MODEL_FILE = /\.(urdf|stl|obj|dae|glb|gltf)$/i;

export function modelFormat(path: string | null | undefined): ModelFormat | null {
  const match = MODEL_FILE.exec(String(path || '').split(/[?#]/, 1)[0]);
  return match ? match[1].toLowerCase() as ModelFormat : null;
}

/** A small, strict XML reader: enough for URDF, and it says where it broke. */
export interface XmlElement { name: string; attrs: Record<string, string>; children: XmlElement[]; text: string }

export class ModelParseError extends Error {}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
const decode = (value: string) => value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, code: string) => {
  if (code[0] !== '#') return ENTITIES[code] ?? whole;
  const point = code[1].toLowerCase() === 'x' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
  return Number.isFinite(point) ? String.fromCodePoint(point) : whole;
});

function lineAt(source: string, index: number) {
  return source.slice(0, index).split('\n').length;
}

export function parseXml(source: string): XmlElement {
  const root: XmlElement = { name: '#document', attrs: {}, children: [], text: '' };
  const stack = [root];
  const tag = /<!--[\s\S]*?-->|<!\[CDATA\[([\s\S]*?)\]\]>|<\?[\s\S]*?\?>|<!DOCTYPE[^>]*>|<\/\s*([^\s>]+)\s*>|<([A-Za-z_][\w:.-]*)((?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(\/?)>/g;
  let last = 0;
  for (let match = tag.exec(source); match; match = tag.exec(source)) {
    const between = source.slice(last, match.index);
    if (between.includes('<')) throw new ModelParseError(`XML is malformed near line ${lineAt(source, last + between.indexOf('<'))}`);
    stack[stack.length - 1].text += decode(between);
    last = tag.lastIndex;
    if (match[1] !== undefined) { stack[stack.length - 1].text += match[1]; continue; }
    if (match[2]) {
      const open = stack.pop();
      if (!open || open === root || open.name !== match[2]) {
        throw new ModelParseError(`Closing tag </${match[2]}> on line ${lineAt(source, match.index)} does not match ${open && open !== root ? `<${open.name}>` : 'any open element'}`);
      }
      continue;
    }
    if (!match[3]) continue;
    const attrs: Record<string, string> = {};
    for (const attr of match[4].matchAll(/([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) attrs[attr[1]] = decode(attr[2] ?? attr[3] ?? '');
    const element: XmlElement = { name: match[3], attrs, children: [], text: '' };
    stack[stack.length - 1].children.push(element);
    if (!match[5]) stack.push(element);
  }
  if (source.slice(last).includes('<')) throw new ModelParseError(`XML is malformed near line ${lineAt(source, last + source.slice(last).indexOf('<'))}`);
  if (stack.length > 1) throw new ModelParseError(`<${stack[stack.length - 1].name}> is never closed`);
  return root;
}

const childrenNamed = (element: XmlElement, name: string) => element.children.filter((child) => child.name === name);
const firstNamed = (element: XmlElement, name: string) => element.children.find((child) => child.name === name);

export type UrdfJointType = 'revolute' | 'continuous' | 'prismatic' | 'fixed' | 'floating' | 'planar';

export interface UrdfJoint {
  name: string;
  type: UrdfJointType;
  parent: string;
  child: string;
  /** SI: radians for rotation, metres for translation. */
  lower: number;
  upper: number;
  mimic?: { joint: string; multiplier: number; offset: number };
}

export interface UrdfSummary {
  name: string;
  links: string[];
  joints: UrdfJoint[];
  /** Mesh and texture references exactly as written, visual and collision. */
  meshRefs: string[];
  textureRefs: string[];
  hasVisual: boolean;
  hasCollision: boolean;
}

const JOINT_TYPES = new Set<UrdfJointType>(['revolute', 'continuous', 'prismatic', 'fixed', 'floating', 'planar']);

function number(value: string | undefined, fallback: number) {
  const parsed = Number.parseFloat(String(value ?? ''));
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Read what a URDF declares. Throws ModelParseError with a readable reason. */
export function parseUrdf(source: string): UrdfSummary {
  const document = parseXml(source);
  const robot = document.children.find((child) => child.name === 'robot');
  if (!robot) throw new ModelParseError('No <robot> element: this is not a URDF file');
  const links = childrenNamed(robot, 'link');
  if (!links.length) throw new ModelParseError('The URDF declares no <link>');
  const linkNames = new Set(links.map((link) => link.attrs.name || ''));
  const meshRefs: string[] = [];
  const textureRefs: string[] = [];
  let hasVisual = false;
  let hasCollision = false;
  const walk = (element: XmlElement) => {
    if (element.name === 'mesh' && element.attrs.filename && !meshRefs.includes(element.attrs.filename)) meshRefs.push(element.attrs.filename);
    if (element.name === 'texture' && element.attrs.filename && !textureRefs.includes(element.attrs.filename)) textureRefs.push(element.attrs.filename);
    element.children.forEach(walk);
  };
  for (const link of links) {
    hasVisual ||= childrenNamed(link, 'visual').length > 0;
    hasCollision ||= childrenNamed(link, 'collision').length > 0;
    walk(link);
  }
  childrenNamed(robot, 'material').forEach(walk);
  const joints = childrenNamed(robot, 'joint').map((joint): UrdfJoint => {
    const name = joint.attrs.name || '';
    const type = (joint.attrs.type || '') as UrdfJointType;
    if (!JOINT_TYPES.has(type)) throw new ModelParseError(`Joint "${name}" has unknown type "${joint.attrs.type ?? ''}"`);
    const parent = firstNamed(joint, 'parent')?.attrs.link || '';
    const child = firstNamed(joint, 'child')?.attrs.link || '';
    for (const [role, link] of [['parent', parent], ['child', child]] as const) {
      if (!linkNames.has(link)) throw new ModelParseError(`Joint "${name}" names ${role} link "${link}", which is not declared`);
    }
    const limit = firstNamed(joint, 'limit');
    const mimic = firstNamed(joint, 'mimic');
    return {
      name, type, parent, child,
      lower: number(limit?.attrs.lower, 0),
      upper: number(limit?.attrs.upper, 0),
      ...(mimic?.attrs.joint ? { mimic: { joint: mimic.attrs.joint, multiplier: number(mimic.attrs.multiplier, 1), offset: number(mimic.attrs.offset, 0) } } : {}),
    };
  });
  return { name: robot.attrs.name || '', links: [...linkNames], joints, meshRefs, textureRefs, hasVisual, hasCollision };
}

/** One slider: shown in degrees or millimetres, stored in SI. */
export interface JointControl {
  name: string;
  type: 'revolute' | 'continuous' | 'prismatic';
  unit: 'deg' | 'mm';
  /** Display units. */
  min: number;
  max: number;
  step: number;
  /** SI home value: the URDF zero, or mid-range when zero lies outside the limits. */
  home: number;
  mimic?: string;
}

const DEG = 180 / Math.PI;

export function jointControl(joint: UrdfJoint): JointControl | null {
  if (joint.type !== 'revolute' && joint.type !== 'continuous' && joint.type !== 'prismatic') return null;
  const rotary = joint.type !== 'prismatic';
  const scale = rotary ? DEG : 1000;
  // URDF leaves continuous joints unbounded; a slider shows one full turn.
  const declared = joint.type !== 'continuous' && joint.upper > joint.lower;
  const lower = declared ? joint.lower : joint.type === 'prismatic' ? 0 : -Math.PI;
  const upper = declared ? joint.upper : joint.type === 'prismatic' ? 0 : Math.PI;
  const min = round(lower * scale);
  const max = round(upper * scale);
  const span = max - min;
  return {
    name: joint.name,
    type: joint.type,
    unit: rotary ? 'deg' : 'mm',
    min, max,
    step: span <= 0 ? 1 : span <= 20 ? 0.1 : span <= 200 ? 0.5 : 1,
    // A shoulder limited to 5°…175° has no zero; resting it on a limit stacks
    // the links on top of each other, mid-range shows the mechanism.
    home: lower <= 0 && upper >= 0 ? 0 : (lower + upper) / 2,
    ...(joint.mimic ? { mimic: joint.mimic.joint } : {}),
  };
}

function round(value: number) {
  return Math.round(value * 1000) / 1000;
}

export function toDisplay(control: JointControl, si: number) {
  return round(si * (control.unit === 'deg' ? DEG : 1000));
}

export function fromDisplay(control: JointControl, display: number) {
  return display / (control.unit === 'deg' ? DEG : 1000);
}

export function formatJoint(control: JointControl, si: number) {
  const value = toDisplay(control, si);
  return `${Math.abs(value) < 10 && control.unit === 'mm' ? value.toFixed(1) : value.toFixed(control.unit === 'deg' ? 1 : 0)}${control.unit === 'deg' ? '°' : ' mm'}`;
}

/** The SI value a sweep reaches `seconds` after starting at `from`: through the whole range and back. */
export function sweepValue(control: JointControl, seconds: number, from = control.home, period = 6) {
  const lower = fromDisplay(control, control.min);
  const upper = fromDisplay(control, control.max);
  if (!(upper > lower)) return control.home;
  const mid = (lower + upper) / 2;
  const half = (upper - lower) / 2;
  // Start from where the joint is, so the motion does not jump.
  const start = Math.asin(Math.max(-1, Math.min(1, (from - mid) / half)));
  return mid + half * Math.sin(start + (2 * Math.PI * seconds) / period);
}

/**
 * Resolve a reference made inside a referenced file (a glTF buffer, a COLLADA
 * texture) to a reference the model-asset route understands: relative to the
 * referencing file, keeping a `package://` prefix.
 */
export function joinModelRef(baseRef: string, uri: string) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(uri) || uri.startsWith('/')) return uri;
  const packageMatch = /^(package:\/\/[^/]+\/)(.*)$/.exec(baseRef);
  const prefix = packageMatch ? packageMatch[1] : '';
  const parts = (packageMatch ? packageMatch[2] : baseRef).split('/').slice(0, -1);
  for (const part of decodeURI(uri).split('/')) {
    if (part === '..') {
      if (parts.length && parts[parts.length - 1] !== '..') parts.pop();
      else if (!prefix) parts.push('..');
    } else if (part && part !== '.') parts.push(part);
  }
  return prefix + parts.join('/');
}

/** External files a glTF JSON references: buffers and images, not data URIs. */
export function gltfExternalUris(json: { buffers?: Array<{ uri?: string }>; images?: Array<{ uri?: string }> }) {
  return [...(json.buffers ?? []), ...(json.images ?? [])]
    .map((entry) => entry.uri)
    .filter((uri): uri is string => Boolean(uri) && !uri!.startsWith('data:'));
}

/** Image files a COLLADA document names in <init_from>. */
export function colladaImageUris(text: string) {
  return [...new Set([...text.matchAll(/<init_from>\s*([^<\s][^<]*?)\s*<\/init_from>/g)].map((match) => match[1])
    .filter((uri) => /\.(png|jpe?g|webp)$/i.test(uri)))];
}

/**
 * A glTF JSON plus its external buffers and images as one GLB, so the loader
 * never fetches anything itself (the hosted page only connects to its own
 * origin, and only with the session's credentials).
 */
export function packGlb(json: Record<string, unknown> & {
  buffers?: Array<{ uri?: string; byteLength: number }>;
  bufferViews?: Array<{ buffer: number; byteOffset?: number; byteLength: number }>;
  images?: Array<{ uri?: string; bufferView?: number; mimeType?: string }>;
}, files: Map<string, ArrayBuffer>): ArrayBuffer {
  const doc = JSON.parse(JSON.stringify(json)) as typeof json;
  const chunks: Uint8Array[] = [];
  let length = 0;
  const append = (bytes: Uint8Array) => {
    const offset = length;
    chunks.push(bytes);
    length += bytes.byteLength;
    const pad = (4 - (length % 4)) % 4;
    if (pad) { chunks.push(new Uint8Array(pad)); length += pad; }
    return offset;
  };
  const bufferOffsets = (doc.buffers ?? []).map((buffer) => {
    const bytes = bufferBytes(buffer.uri, files);
    if (!bytes) throw new ModelParseError(`glTF buffer is missing: ${buffer.uri ?? '(embedded)'}`);
    return append(bytes.subarray(0, buffer.byteLength));
  });
  doc.bufferViews = (doc.bufferViews ?? []).map((view) => ({ ...view, buffer: 0, byteOffset: (view.byteOffset ?? 0) + bufferOffsets[view.buffer] }));
  doc.images = (doc.images ?? []).map((image) => {
    if (!image.uri) return image;
    const bytes = bufferBytes(image.uri, files);
    if (!bytes) return image;
    doc.bufferViews!.push({ buffer: 0, byteOffset: append(bytes), byteLength: bytes.byteLength });
    const mimeType = image.mimeType || (/\.png$/i.test(image.uri) ? 'image/png' : /\.webp$/i.test(image.uri) ? 'image/webp' : 'image/jpeg');
    return { mimeType, bufferView: doc.bufferViews!.length - 1 };
  });
  doc.buffers = length ? [{ byteLength: length }] : [];
  const bin = new Uint8Array(length);
  let cursor = 0;
  for (const chunk of chunks) { bin.set(chunk, cursor); cursor += chunk.byteLength; }
  let jsonBytes = new TextEncoder().encode(JSON.stringify(doc));
  const jsonPad = (4 - (jsonBytes.byteLength % 4)) % 4;
  if (jsonPad) { const padded = new Uint8Array(jsonBytes.byteLength + jsonPad).fill(0x20); padded.set(jsonBytes); jsonBytes = padded; }
  const total = 12 + 8 + jsonBytes.byteLength + (length ? 8 + length : 0);
  const out = new ArrayBuffer(total);
  const view = new DataView(out);
  const bytes = new Uint8Array(out);
  view.setUint32(0, 0x46546c67, true); // 'glTF'
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonBytes.byteLength, true);
  view.setUint32(16, 0x4e4f534a, true); // 'JSON'
  bytes.set(jsonBytes, 20);
  if (length) {
    const at = 20 + jsonBytes.byteLength;
    view.setUint32(at, length, true);
    view.setUint32(at + 4, 0x004e4942, true); // 'BIN\0'
    bytes.set(bin, at + 8);
  }
  return out;
}

function bufferBytes(uri: string | undefined, files: Map<string, ArrayBuffer>): Uint8Array | null {
  if (!uri) return null;
  const data = /^data:[^,]*?(;base64)?,(.*)$/s.exec(uri);
  if (data) {
    if (!data[1]) return new TextEncoder().encode(decodeURIComponent(data[2]));
    const binary = atob(data[2]);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  }
  const file = files.get(uri);
  return file ? new Uint8Array(file) : null;
}

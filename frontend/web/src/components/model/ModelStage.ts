/**
 * One three.js stage: renderer, camera, soft studio lighting, a ground grid,
 * orbit controls and on-demand rendering. The React viewer owns the model and
 * the joint values; this class only draws them.
 */
import {
  ACESFilmicToneMapping, AxesHelper, Box3, Box3Helper, Color, DirectionalLight, Group, HemisphereLight, Mesh,
  MeshStandardMaterial, Object3D, PCFShadowMap, PerspectiveCamera, PlaneGeometry, PMREMGenerator, Raycaster,
  Scene, ShadowMaterial, SRGBColorSpace, Vector2, Vector3, WebGLRenderer, GridHelper, type Material,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import type { URDFRobot } from 'urdf-loader';

export interface StageOptions {
  /** A small, static-camera card view that turns slowly and does not take input. */
  thumbnail?: boolean;
  /** URDF and STL are Z-up; three.js is Y-up. */
  zUp?: boolean;
  onHover?: (hover: { name: string; x: number; y: number } | null) => void;
}

const isDark = () => typeof document !== 'undefined' && document.documentElement.dataset.theme === 'dark';

function cssColor(name: string, fallback: string) {
  if (typeof document === 'undefined') return new Color(fallback);
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value ? new Color(`rgb(${value.split(/\s+/).join(',')})`) : new Color(fallback);
}

/** 1, 2 or 5 times a power of ten: grid cells read as round lengths. */
export function niceStep(value: number) {
  if (!(value > 0)) return 0.1;
  const power = 10 ** Math.floor(Math.log10(value));
  const scaled = value / power;
  return (scaled < 1.5 ? 1 : scaled < 3.5 ? 2 : scaled < 7.5 ? 5 : 10) * power;
}

export function disposeObject(root: Object3D) {
  const materials = new Set<Material>();
  root.traverse((child) => {
    const mesh = child as Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry?.dispose();
    (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).forEach((material) => material && materials.add(material));
  });
  materials.forEach((material) => {
    for (const value of Object.values(material)) if (value && typeof value === 'object' && 'isTexture' in value) (value as { dispose(): void }).dispose();
    material.dispose();
  });
}

export class ModelStage {
  readonly renderer: WebGLRenderer;
  private readonly scene = new Scene();
  private readonly camera = new PerspectiveCamera(35, 1, 0.001, 1000);
  private readonly controls: OrbitControls | null;
  private readonly turntable = new Group();
  private readonly mount = new Group();
  private readonly key = new DirectionalLight(0xffffff, 1.7);
  private readonly hemi = new HemisphereLight(0xffffff, 0xd8d2c4, 0.55);
  private readonly ground = new Mesh(new PlaneGeometry(1, 1), new ShadowMaterial({ opacity: 0.14 }));
  private grid: GridHelper | null = null;
  private axes: AxesHelper | null = null;
  private highlight: Box3Helper | null = null;
  private hovered: Object3D | null = null;
  private model: Object3D | null = null;
  private robot: URDFRobot | null = null;
  private frame = 0;
  private dirty = true;
  private active = true;
  private lastTick = 0;
  private radius = 1;
  private pointer: Vector2 | null = null;
  private pointerClient = { x: 0, y: 0 };
  private readonly raycaster = new Raycaster();
  private readonly reducedMotion = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  private readonly resize: ResizeObserver;
  private readonly colliderMaterial = new MeshStandardMaterial({ color: 0xd99a2b, transparent: true, opacity: 0.55, roughness: 0.6, depthWrite: false });

  constructor(private readonly container: HTMLElement, private readonly options: StageOptions = {}) {
    const thumbnail = Boolean(options.thumbnail);
    this.renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: thumbnail ? 'low-power' : 'default' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, thumbnail ? 1.5 : 2));
    this.renderer.outputColorSpace = SRGBColorSpace;
    this.renderer.toneMapping = ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.shadowMap.enabled = !thumbnail;
    this.renderer.shadowMap.type = PCFShadowMap;
    this.renderer.setClearColor(0x000000, 0);
    const canvas = this.renderer.domElement;
    canvas.style.display = 'block';
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.touchAction = thumbnail ? 'auto' : 'none';
    container.appendChild(canvas);

    const pmrem = new PMREMGenerator(this.renderer);
    const room = new RoomEnvironment();
    this.scene.environment = pmrem.fromScene(room, 0.04).texture;
    this.scene.environmentIntensity = 0.55;
    room.dispose();
    pmrem.dispose();

    this.key.castShadow = !thumbnail;
    this.key.shadow.mapSize.set(1024, 1024);
    this.key.shadow.bias = -0.0004;
    this.key.shadow.radius = 6;
    this.scene.add(this.hemi, this.key, this.key.target);
    const fill = new DirectionalLight(0xf3efe6, 0.45);
    fill.position.set(-3, 2, -2);
    this.scene.add(fill);
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.receiveShadow = true;
    this.scene.add(this.ground);
    this.turntable.add(this.mount);
    this.scene.add(this.turntable);

    if (!thumbnail) {
      this.controls = new OrbitControls(this.camera, canvas);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = 0.12;
      this.controls.screenSpacePanning = true;
      this.controls.addEventListener('change', () => { this.dirty = true; });
      canvas.addEventListener('pointermove', this.onPointerMove);
      canvas.addEventListener('pointerleave', this.onPointerLeave);
    } else {
      this.controls = null;
    }
    this.applyTheme();
    this.resize = new ResizeObserver(() => this.fitSize());
    this.resize.observe(container);
    this.fitSize();
    this.frame = requestAnimationFrame(this.tick);
  }

  applyTheme() {
    const dark = isDark();
    this.buildGrid();
    this.hemi.groundColor.set(dark ? 0x3a3631 : 0xd8d2c4);
    this.hemi.intensity = dark ? 0.7 : 0.55;
    (this.ground.material as ShadowMaterial).opacity = dark ? 0.35 : 0.14;
    this.dirty = true;
  }

  private gridSpan = { size: 0, divisions: 0 };

  private buildGrid() {
    const { size, divisions } = this.gridSpan;
    this.grid?.removeFromParent();
    this.grid?.geometry.dispose();
    (this.grid?.material as Material | undefined)?.dispose();
    this.grid = null;
    if (!size) return;
    const dark = isDark();
    this.grid = new GridHelper(size, divisions, cssColor('--silver', dark ? '#787c82' : '#c3c7cc'), cssColor('--line', dark ? '#2d2e32' : '#dcdee1'));
    const material = this.grid.material as Material;
    material.transparent = true;
    material.opacity = dark ? 0.75 : 0.9;
    material.depthWrite = false;
    this.scene.add(this.grid);
  }

  setModel(root: Object3D, robot: URDFRobot | null) {
    if (this.model) this.mount.remove(this.model);
    this.model = root;
    this.robot = robot;
    this.mount.rotation.set(this.options.zUp ? -Math.PI / 2 : 0, 0, 0);
    this.mount.position.set(0, 0, 0);
    this.mount.add(root);
    root.traverse((node) => {
      if (!(node as Object3D & { isURDFCollider?: boolean }).isURDFCollider) return;
      node.traverse((child) => {
        const mesh = child as Mesh;
        if (mesh.isMesh) { mesh.material = this.colliderMaterial; mesh.castShadow = false; }
      });
      node.visible = false;
    });
    this.axes?.removeFromParent();
    this.axes = null;
    this.fit();
  }

  /** Frame the whole model: sit it on the grid, size the grid and lights, aim the camera. */
  fit() {
    if (!this.model) return;
    this.mount.position.set(0, 0, 0);
    this.mount.updateMatrixWorld(true);
    // Measure the model alone, not its axes marker.
    this.axes?.removeFromParent();
    const box = new Box3().setFromObject(this.mount, true);
    if (this.axes) this.model.add(this.axes);
    if (box.isEmpty()) return;
    const center = box.getCenter(new Vector3());
    this.mount.position.set(-center.x, -box.min.y, -center.z);
    this.mount.updateMatrixWorld(true);
    const size = box.getSize(new Vector3());
    const radius = Math.max(size.length() / 2, 1e-3);
    this.radius = radius;
    const cell = niceStep(Math.max(size.x, size.z, size.y) / 6);
    const divisions = Math.max(10, Math.min(60, Math.ceil((radius * 6) / cell / 2) * 2));
    this.gridSpan = { size: cell * divisions, divisions };
    this.ground.scale.setScalar(cell * divisions);
    if (!this.axes) {
      this.axes = new AxesHelper(1);
      this.axes.renderOrder = 2;
      (this.axes.material as Material).depthTest = false;
      (this.model as Object3D).add(this.axes);
    }
    this.axes.scale.setScalar(Math.max(size.x, size.y, size.z) * 0.18);
    this.applyTheme();

    const height = size.y / 2;
    const target = new Vector3(0, height, 0);
    const fov = (this.camera.fov * Math.PI) / 180;
    const direction = new Vector3(1.1, 0.75, 1.35).normalize();
    // Frame what the camera can actually see: the box itself, or for a
    // turning card the cylinder the box sweeps, so a long, flat mechanism
    // fills the view at every angle instead of floating in the middle.
    let distance = radius / Math.sin(fov / 2);
    {
      const sweep = Math.hypot(size.x, size.z) / 2;
      const corners = this.options.thumbnail
        ? Array.from({ length: 32 }, (_, index) => new Vector3(
          Math.cos((index * Math.PI) / 8) * sweep, index < 16 ? 0 : size.y, Math.sin((index * Math.PI) / 8) * sweep))
        : [0, 1, 2, 3, 4, 5, 6, 7].map((index) => new Vector3(
          ((index & 1) - 0.5) * size.x, index & 2 ? size.y : 0, (((index >> 2) & 1) - 0.5) * size.z));
      for (let pass = 0; pass < 4; pass += 1) {
        this.camera.position.copy(target).addScaledVector(direction, distance);
        this.camera.lookAt(target);
        this.camera.updateMatrixWorld(true);
        this.camera.updateProjectionMatrix();
        const extent = Math.max(...corners.map((corner) => {
          const ndc = corner.clone().project(this.camera);
          return Math.max(Math.abs(ndc.x), Math.abs(ndc.y));
        }));
        distance *= Math.max(0.5, Math.min(2, extent / (this.options.thumbnail ? 0.86 : 0.78)));
      }
    }
    this.camera.position.copy(target).addScaledVector(direction, distance);
    this.camera.near = distance / 200;
    this.camera.far = distance * 50;
    this.camera.updateProjectionMatrix();
    this.camera.lookAt(target);
    if (this.controls) {
      this.controls.target.copy(target);
      this.controls.minDistance = radius * 0.2;
      this.controls.maxDistance = distance * 8;
      this.controls.update();
    }
    this.key.position.set(radius * 2.2, radius * 3.5, radius * 1.6);
    this.key.target.position.set(0, 0, 0);
    const shadow = this.key.shadow.camera;
    shadow.left = shadow.bottom = -radius * 1.8;
    shadow.right = shadow.top = radius * 1.8;
    shadow.near = radius * 0.1;
    shadow.far = radius * 10;
    shadow.updateProjectionMatrix();
    this.dirty = true;
  }

  setJoint(name: string, value: number) {
    if (this.robot?.setJointValue(name, value)) this.afterPose();
  }

  setJoints(values: Record<string, number>) {
    if (this.robot?.setJointValues(values)) this.afterPose();
  }

  private afterPose() {
    if (this.hovered) this.showHighlight(this.hovered);
    this.dirty = true;
  }

  setGeometry(mode: 'visual' | 'collision') {
    if (!this.robot) return;
    for (const visual of Object.values(this.robot.visual)) visual.visible = mode === 'visual';
    // urdf-loader keys only named elements; walk the tree for the rest.
    this.robot.traverse((child) => {
      const node = child as Object3D & { isURDFVisual?: boolean; isURDFCollider?: boolean };
      if (node.isURDFVisual) node.visible = mode === 'visual';
      if (node.isURDFCollider) node.visible = mode === 'collision';
    });
    this.dirty = true;
  }

  setActive(active: boolean) {
    this.active = active;
    this.dirty = true;
  }

  invalidate() {
    this.dirty = true;
  }

  /** The current view as a PNG on the page background. */
  screenshot(): Promise<Blob | null> {
    this.render();
    const source = this.renderer.domElement;
    const canvas = document.createElement('canvas');
    canvas.width = source.width;
    canvas.height = source.height;
    const context = canvas.getContext('2d');
    if (!context) return Promise.resolve(null);
    const background = cssColor('--surface-0', '#f5f5f4');
    context.fillStyle = `#${background.getHexString()}`;
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(source, 0, 0);
    return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
  }

  dispose() {
    cancelAnimationFrame(this.frame);
    this.resize.disconnect();
    this.controls?.dispose();
    const canvas = this.renderer.domElement;
    canvas.removeEventListener('pointermove', this.onPointerMove);
    canvas.removeEventListener('pointerleave', this.onPointerLeave);
    if (this.model) this.mount.remove(this.model);
    this.axes?.dispose();
    this.axes?.removeFromParent();
    this.gridSpan = { size: 0, divisions: 0 };
    this.buildGrid();
    this.highlight?.dispose();
    this.ground.geometry.dispose();
    this.colliderMaterial.dispose();
    this.scene.environment?.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    canvas.remove();
  }

  private fitSize() {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  private readonly onPointerMove = (event: PointerEvent) => {
    if (event.pointerType === 'touch' || event.buttons) { this.clearHover(); return; }
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer = new Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    this.pointerClient = { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  private readonly onPointerLeave = () => this.clearHover();

  private clearHover() {
    this.pointer = null;
    if (this.hovered) {
      this.hovered = null;
      this.highlight?.removeFromParent();
      this.options.onHover?.(null);
      this.dirty = true;
    }
  }

  private pick() {
    if (!this.pointer || !this.model) return;
    const pointer = this.pointer;
    this.pointer = null;
    this.raycaster.setFromCamera(pointer, this.camera);
    const hit = this.raycaster.intersectObject(this.model, true).find((entry) => {
      for (let node: Object3D | null = entry.object; node; node = node.parent) if (!node.visible) return false;
      return (entry.object as Mesh).isMesh;
    });
    let link: Object3D | null = null;
    for (let node: Object3D | null = hit?.object ?? null; node; node = node.parent) {
      if ((node as Object3D & { isURDFLink?: boolean }).isURDFLink) { link = node; break; }
    }
    const name = link ? (link as Object3D & { urdfName?: string }).urdfName || link.name : hit ? hit.object.name : '';
    if (!hit || !name) { this.clearHover(); return; }
    if (link !== this.hovered || !link) {
      this.hovered = link;
      if (link) this.showHighlight(link); else this.highlight?.removeFromParent();
      this.dirty = true;
    }
    this.options.onHover?.({ name, ...this.pointerClient });
  }

  /** Outline only this link's own shapes, not the links hanging below it. */
  private showHighlight(link: Object3D) {
    const box = new Box3();
    link.updateWorldMatrix(true, true);
    for (const child of link.children) {
      const node = child as Object3D & { isURDFVisual?: boolean; isURDFCollider?: boolean };
      if ((node.isURDFVisual || node.isURDFCollider) && node.visible) box.expandByObject(node, true);
    }
    if (box.isEmpty()) { this.highlight?.removeFromParent(); return; }
    if (!this.highlight) {
      this.highlight = new Box3Helper(box, new Color(cssColor('--accent', '#b08d3c')));
      (this.highlight.material as Material).depthTest = false;
      this.highlight.renderOrder = 3;
    } else {
      this.highlight.box.copy(box);
    }
    if (!this.highlight.parent) this.scene.add(this.highlight);
  }

  private render() {
    this.renderer.render(this.scene, this.camera);
  }

  private readonly tick = (now: number) => {
    this.frame = requestAnimationFrame(this.tick);
    if (!this.active) return;
    const elapsed = this.lastTick ? Math.min(0.1, (now - this.lastTick) / 1000) : 0;
    if (this.options.thumbnail) {
      // Thirty frames a second is plenty for a slow turn on a card.
      if (now - this.lastTick < 33) return;
      if (!this.reducedMotion) this.turntable.rotation.y += elapsed * 0.32;
      this.dirty = true;
    }
    this.lastTick = now;
    if (this.controls?.update()) this.dirty = true;
    this.pick();
    if (!this.dirty) return;
    this.dirty = false;
    this.render();
  };

  get size() {
    return this.radius;
  }
}

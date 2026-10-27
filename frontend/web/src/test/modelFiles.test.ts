import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  colladaImageUris, formatJoint, fromDisplay, gltfExternalUris, jointControl, joinModelRef, modelFormat, packGlb,
  parseUrdf, sweepValue, toDisplay, ModelParseError, type JointControl,
} from '../lib/modelFiles';

// A real agent-made URDF: a five-bar platform whose loop closes outside the tree.
const FIVEBAR = readFileSync(fileURLToPath(new URL('../../../../tests/fixtures/robot/adjustable_fivebar.urdf', import.meta.url)), 'utf8');

describe('URDF reading', () => {
  it('reads the five-bar fixture: links, joints, limits and mimic', () => {
    const urdf = parseUrdf(FIVEBAR);
    expect(urdf.name).toBe('adjustable_fivebar');
    expect(urdf.links).toHaveLength(8);
    expect(urdf.joints.map((joint) => `${joint.name}:${joint.type}`)).toEqual([
      'left_slide:prismatic', 'left_shoulder:revolute', 'left_elbow:revolute',
      'right_slide:prismatic', 'right_shoulder:revolute', 'right_elbow:revolute', 'tool_mount:fixed',
    ]);
    expect(urdf.joints.find((joint) => joint.name === 'right_slide')?.mimic).toEqual({ joint: 'left_slide', multiplier: 1, offset: 0 });
    expect(urdf.meshRefs).toEqual([]);
    expect(urdf.hasVisual && urdf.hasCollision).toBe(true);
  });

  it('collects mesh and texture references exactly as written', () => {
    const urdf = parseUrdf(`<?xml version="1.0"?><robot name="arm">
      <material name="m"><texture filename="textures/a.png"/></material>
      <link name="base"><visual><geometry><mesh filename="package://arm/meshes/base.stl" scale="0.001 0.001 0.001"/></geometry></visual>
        <collision><geometry><mesh filename="../meshes/base_collision.obj"/></geometry></collision></link>
      <link name="tool"><visual><geometry><cylinder radius="0.01" length="0.1"/></geometry></visual></link>
      <joint name="spin" type="continuous"><parent link="base"/><child link="tool"/><axis xyz="0 0 1"/></joint>
    </robot>`);
    expect(urdf.meshRefs).toEqual(['package://arm/meshes/base.stl', '../meshes/base_collision.obj']);
    expect(urdf.textureRefs).toEqual(['textures/a.png']);
  });

  it.each([
    ['<robot name="x"><link name="a"></robot>', /does not match/],
    ['<robot name="x"><link name="a">', /never closed/],
    ['<model/>', /No <robot>/],
    ['<robot name="x"/>', /no <link>/],
    ['<robot name="x"><link name="a"/><joint name="j" type="fixed"><parent link="a"/><child link="b"/></joint></robot>', /child link "b"/],
    ['<robot name="x"><link name="a"/><link name="b"/><joint name="j" type="hinge"><parent link="a"/><child link="b"/></joint></robot>', /unknown type "hinge"/],
  ])('explains why %s cannot be read', (source, reason) => {
    expect(() => parseUrdf(source)).toThrow(ModelParseError);
    expect(() => parseUrdf(source)).toThrow(reason);
  });
});

describe('joint sliders', () => {
  const controls = Object.fromEntries(parseUrdf(FIVEBAR).joints.map((joint) => [joint.name, jointControl(joint)]));

  it('bounds each slider by the URDF limits in degrees or millimetres', () => {
    expect(controls.left_slide).toMatchObject({ unit: 'mm', min: 0, max: 60, home: 0 });
    expect(controls.left_shoulder).toMatchObject({ unit: 'deg', min: 5, max: 175 });
    expect(controls.left_elbow).toMatchObject({ unit: 'deg', min: -180, max: 180, home: 0 });
    expect(controls.right_slide?.mimic).toBe('left_slide');
    expect(controls.tool_mount).toBeNull();
  });

  it('rests a joint whose range excludes zero at mid-range, not on a limit', () => {
    expect(toDisplay(controls.left_shoulder!, controls.left_shoulder!.home)).toBe(90);
  });

  it('converts display units back to SI and formats them', () => {
    const shoulder = controls.left_shoulder!;
    expect(fromDisplay(shoulder, 90)).toBeCloseTo(Math.PI / 2);
    expect(formatJoint(shoulder, Math.PI / 4)).toBe('45.0°');
    expect(fromDisplay(controls.left_slide!, 45)).toBeCloseTo(0.045);
    expect(formatJoint(controls.left_slide!, 0.0452)).toBe('45 mm');
  });

  it('gives a continuous joint one full turn', () => {
    expect(jointControl({ name: 'w', type: 'continuous', parent: 'a', child: 'b', lower: 0, upper: 0 }))
      .toMatchObject({ unit: 'deg', min: -180, max: 180, home: 0 });
  });

  it('sweeps within the range, starting from the current value', () => {
    const shoulder = controls.left_shoulder as JointControl;
    const start = fromDisplay(shoulder, 30);
    expect(sweepValue(shoulder, 0, start)).toBeCloseTo(start);
    for (let t = 0; t < 6; t += 0.25) {
      const value = toDisplay(shoulder, sweepValue(shoulder, t, start));
      expect(value).toBeGreaterThanOrEqual(5 - 1e-6);
      expect(value).toBeLessThanOrEqual(175 + 1e-6);
    }
  });
});

describe('model files and their references', () => {
  it('recognises model formats by suffix', () => {
    expect(['robot/a.URDF', 'm.stl', 'm.obj', 'm.dae', 'm.glb', 'm.gltf', 'm.png', 'm.urdf.xacro'].map(modelFormat))
      .toEqual(['urdf', 'stl', 'obj', 'dae', 'glb', 'gltf', null, null]);
  });

  it('resolves nested references relative to the referencing file, keeping package://', () => {
    expect(joinModelRef('../meshes/arm.gltf', 'arm.bin')).toBe('../meshes/arm.bin');
    expect(joinModelRef('package://arm/meshes/a.dae', '../textures/t.png')).toBe('package://arm/textures/t.png');
    expect(joinModelRef('meshes/a.gltf', 'tex%20one.png')).toBe('meshes/tex one.png');
    expect(joinModelRef('meshes/a.gltf', 'data:application/octet-stream;base64,AA==')).toMatch(/^data:/);
  });

  it('finds glTF buffers/images and COLLADA textures, skipping embedded data', () => {
    expect(gltfExternalUris({ buffers: [{ uri: 'a.bin' }, { uri: 'data:,x' }], images: [{ uri: 't.png' }, {}] })).toEqual(['a.bin', 't.png']);
    expect(colladaImageUris('<image><init_from>tex/a.png</init_from></image><init_from>node</init_from>')).toEqual(['tex/a.png']);
  });

  it('packs a glTF and its external buffer into one GLB the loader can parse offline', () => {
    const buffer = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]).buffer;
    const glb = packGlb({ asset: { version: '2.0' }, buffers: [{ uri: 'a.bin', byteLength: 8 }], bufferViews: [{ buffer: 0, byteOffset: 4, byteLength: 4 }] },
      new Map([['a.bin', buffer]]));
    const view = new DataView(glb);
    expect(view.getUint32(0, true)).toBe(0x46546c67);
    expect(view.getUint32(8, true)).toBe(glb.byteLength);
    const jsonLength = view.getUint32(12, true);
    const json = JSON.parse(new TextDecoder().decode(new Uint8Array(glb, 20, jsonLength)));
    expect(json.buffers).toEqual([{ byteLength: 8 }]);
    expect(json.bufferViews[0]).toEqual({ buffer: 0, byteOffset: 4, byteLength: 4 });
    expect([...new Uint8Array(glb, 20 + jsonLength + 8 + 4, 4)]).toEqual([5, 6, 7, 8]);
    expect(() => packGlb({ buffers: [{ uri: 'missing.bin', byteLength: 1 }] }, new Map())).toThrow(/missing.bin/);
  });
});

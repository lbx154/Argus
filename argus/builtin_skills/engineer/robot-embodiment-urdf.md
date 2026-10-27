---
name: "Robot Body and Mechanism as a Loadable URDF"
description: "设计或分析机器人本体、连杆机构时，交付可加载的 URDF、检查脚本、多姿态预览图和说明；审阅者据此复核。 When work designs or analyses a robot body or mechanism, deliver robot/<name>.urdf with geometry-derived mass and inertia, a sidecar for closed chains, a script that parses and checks it, rendered previews in several joint poses, and a README. Reviewers use the same checks."
---

# Robot Body and Mechanism as a Loadable URDF

Use when the result is, or depends on, a physical body: an arm, a leg, a
gripper, a linkage, a mobile base. A table of link lengths and a paragraph do
not let anyone see whether the design closes, collides or reaches; a model that
loads and renders does, and it is what the next simulation or controller will
consume. The model is the primary deliverable; the prose explains it.

## What to deliver

```
robot/
  <name>.urdf             # the body
  <name>.closure.json     # only for closed chains, see below
  meshes/                 # only if links use meshes; relative paths
  check_<name>.py         # loads, checks, renders
  validation.json         # raw output of the last check run
  previews/overview.png   # several poses in one image, linked first in README
  previews/pose-<k>.png
  README.md
```

The web viewer renders `.urdf`, `.stl`, `.obj` and `.glb` directly. Keep meshes
beside the URDF and reference them by relative path (`meshes/upper_arm.stl`),
never `package://` or an absolute path, so the model opens anywhere the folder
is copied.

## Building the URDF

- SI units throughout: metres, kilograms, radians. Convert millimetre meshes
  with the `scale` attribute rather than by editing numbers by hand.
- Every link that has a body gets `<visual>` and `<collision>` geometry. Use
  primitives (box, cylinder, sphere) unless a mesh is the point of the work;
  collision may be a simpler envelope than the visual.
- Mass comes from geometry: volume times a stated density, or a stated mass
  budget split across parts. Inertia comes from the same shape about its centre
  of mass, with `<inertial><origin>` at that centre. Box: `Ixx = m(y²+z²)/12`
  and cyclic. Solid cylinder along z: `Izz = m r²/2`, `Ixx = Iyy = m(3r²+h²)/12`.
  Sphere: `2/5 m r²`. For a mesh, `trimesh` `mass_properties` on a watertight
  mesh. Composite parts use the parallel-axis theorem. Never type an inertia
  that no shape produces, and state every density or budget as an assumption.
- Every revolute and prismatic joint has `lower < upper`, `effort > 0` and
  `velocity > 0` from the design (actuator datasheet, stated assumption, or the
  study's range), with the source in the README. `continuous` joints still need
  effort and velocity. Coupled joints use `<mimic>`.
- A virtual link with no body (a tool frame) may have no inertial; any link
  with an inertial has positive mass.

URDF is a tree. A closed chain (five-bar, parallel gripper, four-bar knee) is
cut at one joint: the URDF holds the open tree, and `<name>.closure.json` names
the two links, the frames on each, the constraint type (point, hinge axis) and
how the loader enforces it (PyBullet `createConstraint`, a MuJoCo `equality`).
Say plainly that the bare URDF is not the closed mechanism.

## The check script

The script must actually parse the file, not grep it. Prefer `yourdfpy`
(pure Python, works offline once installed in the project environment); fall
back to `xml.etree.ElementTree` with your own forward kinematics if it cannot
be installed. It checks, and writes the raw results to `validation.json`:

- exactly one root link; every other link is the child of exactly one joint;
  every joint's parent and child exist; names are unique;
- limits as above, and each preview pose lies within them;
- every mass is positive; every inertia matrix is positive definite and
  satisfies the triangle inequalities (`Ixx + Iyy ≥ Izz` and cyclic);
- every mesh file exists at its relative path;
- for a closed chain, the closure gap at each preview pose after the loader
  solves it, against a stated tolerance.

It exits non-zero on any failure. Run it in the project environment and keep
its output; a script that was written but never run is not a check.

## Previews

Render from the same URDF the check loaded, in at least three joint poses that
show the range: neutral, near the limits, and the configurations the study is
about. Headless options: PyBullet in `DIRECT` mode with `getCameraImage`, or
forward kinematics drawn with matplotlib `Poly3DCollection`. Label each panel
with its joint values, keep the camera fixed across panels, and colour the
chains or subsystems consistently. Put an `overview.png` combining the poses
first in the README so it is what a person sees when the result opens.

## README

One overview image, a table of files, a table of joints (type, range, effort,
velocity, driven or passive, source of each value), where the dimensions came
from, how mass and inertia were derived and which values are assumptions, the
closure for closed chains, the command that rebuilds and rechecks everything,
and what the model is not evidence of (strength, actuator sizing, contact
dynamics) unless the work tested it.

## Reviewing a body model

Run the check script yourself and read `validation.json`. Open the previews and
confirm they come from the URDF (same link count, dimensions matching the
joint origins and geometry) and that the poses shown are the ones claimed.
Compare the README's dimensions, masses and ranges with the URDF numbers.
Recompute one inertia from its geometry. A model that does not load, an
invented inertia, an undocumented closed chain or previews that disagree with
the file are material defects.

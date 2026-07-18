# Figure brief: dense intelligence across the horizon

- **Reader question:** How do recurrent roles, Manager-controlled stages, persistent
  state, and a future training flywheel interact over a long horizon?
- **Claim:** Argus extends the effective task horizon by repeating a four-role state
  machine inside non-monotonic stages, retaining reviewed state across hold and
  rollback decisions, and producing trajectories that could support future model
  training; a session-limited agent remains sparse and stalls.
- **Source:** The runtime objects formalized in Sections 3--4. This is a conceptual
  illustration rather than measured data.
- **Encoding:** A fully colored semi-illustrated mountain represents task horizon.
  A four-node cycle represents Manager, Planner, Engineer, and Reviewer as a state
  machine repeated within every Stage. Eight numbered research Stage checkpoints
  follow a mountain-hugging switchback trail with local descents; orange denotes
  rollback and red crosses rejected branches. A binocular-equipped researcher is
  grounded on a connected foreground ridge and looks toward the golden summit flag.
  Distant ridges, clouds, vegetation, and a summit halo enrich the illustration
  without encoding measurements. The lower rail contains persistent runtime state.
  A distinct hypothesis panel maps reviewed trajectories to SFT/RL and native
  long-horizon behavior.
- **Why a figure:** Neither a mountain alone nor a flat architecture diagram shows
  the joint relationship between horizon, activity density, verification, and state
  reuse.
- **Caption takeaway:** Long-horizon progress emerges from repeated role cycles and
  non-monotonic Stage transitions; reviewed runtime data may later support a
  runtime-to-model training flywheel.
- **Scope:** Schematic and not to scale. The training flywheel is a future research
  hypothesis; current reported experiments keep model parameters fixed.
- **Target size:** Full page width after the Dense-Intelligence Task definition.
- **Editable source:** `horizon_mountain.html` for all semantic routes, annotations,
  and the state rail; the illustration source and publication crop are retained.
- **Export:** Image-2 editorial illustration base plus vector PDF/SVG semantic
  overlays and a PNG preview.

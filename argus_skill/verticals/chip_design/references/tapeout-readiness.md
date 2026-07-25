# Tapeout Readiness Boundary

Tapeout readiness requires independent evidence for:

- frozen RTL/netlist/GDS hashes;
- MMMC STA and clock/reset constraints;
- DRC, LVS, antenna, density, and ERC when applicable;
- power distribution and IR/EM methodology;
- SRAM and hard-macro views/provenance;
- IO cells, ESD assumptions, package/bond map, GPIO/power domains;
- foundry/shuttle deck and submission checklist;
- scan/DFT/test access or an explicit scoped limitation;
- firmware/bring-up plan and observable debug interfaces;
- license/IP clearance and export/commercial restrictions;
- archived tools, PDK version, commands, logs, reports, and waivers.

Open-PDK GDS without these items is a physical-design demonstration, not a
tapeout-ready chip. Tapeout-ready does not mean fabricated or silicon-validated.

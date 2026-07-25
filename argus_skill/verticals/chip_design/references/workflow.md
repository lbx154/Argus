# Chip Design Vertical Workflow

The stage order is:

```text
definition
→ architecture
→ environment
→ rtl
→ verification
→ ppa
→ prototype
→ benchmark
→ signoff
```

## Delivery levels

| Level | Minimum certified result |
| --- | --- |
| `rtl_ip` | synthesizable RTL, independent verification, synthesis/PPA, benchmark |
| `fpga` | RTL IP requirements plus implemented bitstream and on-board evidence |
| `gds` | RTL IP requirements plus physical design and STA/DRC/LVS closure |
| `tapeout` | GDS requirements plus antenna, IO/package, foundry and tapeout checklist |

Stages remain present for every level. `prototype/RESULTS.json` may use
`not_applicable` only when the scope does not require physical prototype evidence.

## Artifact root

```text
design/
research/
rtl/ or src/
verification/
formal/
ppa/
physical/
prototype/
benchmark/
signoff/
RESULTS.md
```

Raw outputs belong below their owning stage and must not masquerade as source.

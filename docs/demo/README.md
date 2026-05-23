# docs/demo/

**Pre-pivot demo materials moved to `docs/deprecated/demo/`.** Rewrite
pending acceptance of `docs/adr/ADR-021-comms-first-reframing.md`.

The deprecated deck framed rfmesh as a "cooperative bearing mesh for
RF emitter geolocation." The reframing (ADR-021) puts directional
comms first; the pitch lead is now Advantage #4 (null-steering /
side-lobe rejection of co-channel jammers) — not the triangulation
ellipse story.

When the pitch is rewritten, the new flagship slide is the link-A/B:
*acquire → margin shown → partner unplugged → margin drops →
re-acquire*. The honesty narrative shifts from "ellipse grows when a
node dies" to "link margin drops when a peer moves" — same DNA,
comms framing.

The old materials remain readable under `docs/deprecated/demo/` for
language to lift verbatim where it still applies (the honesty story,
the null-steering A/B math, the €250 BOM).

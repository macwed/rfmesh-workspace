"""DSSS frame format: preamble + sync word + header + payload + CRC (Iter 2).

Wire format (pre-spread, BPSK bits):

::

    | preamble (Np bits)
    | sync word (Nsw bits)
    | header (Nh bits)
    | payload (variable, <= CommsConfig.frame_payload_max_bytes)
    | CRC-16 (16 bits) |

* **Preamble**: known bit pattern; the matched filter in
  ``correlation`` locks on it. Length tuned so a single-frame
  preamble correlator can lock at SNR ~0 dB after spread.
* **Sync word**: short, bit-rotationally distinctive (Barker-like)
  marker that confirms the preamble lock was real and not a
  multipath false-positive. Allows a small Hamming-distance
  tolerance for robustness.
* **Header**: ``src_node_id`` (1 byte; mesh assumes <= 255 nodes),
  ``dst_node_id`` (1 byte), ``sequence_no`` (1 byte; wraps every
  256 frames per src-dst pair -- enough for the latency window),
  ``payload_len_bytes`` (1 byte; bounds the despreader's
  integration window).
* **Payload**: opaque to this module. The node-layer comms loop
  decides what goes here (application bytes, retry markers, peer
  status pings).
* **CRC-16**: CRC-16/CCITT-FALSE (polynomial ``0x1021``, init
  ``0xFFFF``). Same polynomial the servo wire protocol uses; the
  bit-byte routine is **inlined here, not imported** from
  ``rfmesh-servo`` because WD-1 forbids cross-sibling imports.
  The implementations are identical bit-byte; tests in both
  packages assert the same expected CRC values for a fixed input
  to catch drift.

WHY AN INTERNAL CRC RATHER THAN ``zlib.crc32``:

* CRC-16 is 16 bits over the wire; CRC-32 doubles the overhead per
  frame and the bitrate is precious.
* CRC-16/CCITT-FALSE is what the firmware already uses for the
  servo channel; engineers reading both side-by-side see the same
  algorithm.

ITER 0 SKELETON: docstring only. Iter 2 fills ``encode_frame``,
``decode_frame``, and the inlined CRC routine. Golden artefacts
cover: clean round-trip, single-bit-flipped CRC mismatch (must
raise ``FrameDecodeError``), oversize payload refusal, sync-word
recovered with one bit-flip, sync-word refused with two.
"""

from __future__ import annotations

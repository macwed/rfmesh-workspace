"""Diagnostic probe for WS-B-005 feature/scoring tuning.

Builds each canonical scenario from the tests/conftest.py fixtures and
prints (features, scores, label, confidence). Not part of the test
suite; iterates by hand to calibrate the rules thresholds.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

# Add the package src so this script runs without uv editable installs.
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
SRC = WORKSPACE_ROOT / "packages" / "rfmesh-ml" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(WORKSPACE_ROOT / "packages" / "rfmesh-dsp" / "src"))
sys.path.insert(0, str(WORKSPACE_ROOT / "packages" / "rfmesh-contracts" / "src"))

# Inline copies of the conftest synthesisers so the probe is standalone.
SAMPLE_RATE_HZ = 1_000_000.0
N_SAMPLES = 8192
FHSS_HOP_RATE_HZ = 1500.0
FSK_DEVIATION_HZ = 25_000.0
FSK_SYMBOL_RATE_HZ = 9600.0
LORA_BW = 125_000.0


def noise_for_snr(rng, signal, snr_db):
    sp = float(np.mean(np.abs(signal.astype(np.complex128)) ** 2))
    np_ = sp / (10.0 ** (snr_db / 10.0)) if sp > 0 else 1.0
    sigma = math.sqrt(np_ / 2.0)
    r = rng.standard_normal(signal.size).astype(np.float32) * sigma
    i = rng.standard_normal(signal.size).astype(np.float32) * sigma
    return (signal + (r + 1j * i).astype(np.complex64)).astype(np.complex64)


def cw(rng, snr_db):
    n = np.arange(N_SAMPLES, dtype=np.float64)
    tone = np.exp(1j * 2 * math.pi * 100_000 * n / SAMPLE_RATE_HZ).astype(np.complex64)
    return noise_for_snr(rng, tone, snr_db)


def fhss(rng, snr_db):
    hop_freqs = np.linspace(-250_000.0, 250_000.0, 8)
    spd = max(1, int(round(SAMPLE_RATE_HZ / FHSS_HOP_RATE_HZ)))
    nd = N_SAMPLES // spd + 1
    hop_seq = rng.integers(0, len(hop_freqs), size=nd)
    sig = np.empty(N_SAMPLES, dtype=np.complex64)
    phase = 0.0
    idx = 0
    for hi in hop_seq:
        f = float(hop_freqs[hi])
        dphi = 2 * math.pi * f / SAMPLE_RATE_HZ
        nh = min(spd, N_SAMPLES - idx)
        if nh <= 0:
            break
        ph = phase + dphi * np.arange(nh, dtype=np.float64)
        sig[idx : idx + nh] = np.exp(1j * ph).astype(np.complex64)
        phase = (phase + dphi * nh) % (2 * math.pi)
        idx += nh
    return noise_for_snr(rng, sig, snr_db)


def fsk(rng, snr_db):
    sps = max(1, int(round(SAMPLE_RATE_HZ / FSK_SYMBOL_RATE_HZ)))
    ns = N_SAMPLES // sps + 1
    symbols = rng.integers(0, 2, size=ns)
    dev = np.where(symbols == 0, -FSK_DEVIATION_HZ, FSK_DEVIATION_HZ).astype(np.float64)
    sig = np.empty(N_SAMPLES, dtype=np.complex64)
    phase = 0.0
    idx = 0
    for f in dev:
        dphi = 2 * math.pi * float(f) / SAMPLE_RATE_HZ
        nh = min(sps, N_SAMPLES - idx)
        if nh <= 0:
            break
        ph = phase + dphi * np.arange(nh, dtype=np.float64)
        sig[idx : idx + nh] = np.exp(1j * ph).astype(np.complex64)
        phase = (phase + dphi * nh) % (2 * math.pi)
        idx += nh
    return noise_for_snr(rng, sig, snr_db)


def lora(rng, snr_db):
    bw = LORA_BW
    spc = max(2, int(round(SAMPLE_RATE_HZ * (2**7 / bw))))
    t_local = np.arange(spc, dtype=np.float64) / SAMPLE_RATE_HZ
    t_full = spc / SAMPLE_RATE_HZ
    slope = bw / t_full
    f0 = -bw / 2.0
    chirp_phase = 2 * math.pi * f0 * t_local + math.pi * slope * (t_local**2)
    sig = np.exp(1j * chirp_phase).astype(np.complex64)
    sig = np.tile(sig, N_SAMPLES // spc + 1)[:N_SAMPLES]
    return noise_for_snr(rng, sig, snr_db)


def pure_noise(rng):
    r = rng.standard_normal(N_SAMPLES).astype(np.float32)
    i = rng.standard_normal(N_SAMPLES).astype(np.float32)
    return ((r + 1j * i) / math.sqrt(2.0)).astype(np.complex64)


from rfmesh_ml.features import extract_features  # noqa: E402
from rfmesh_ml.rules import classify_from_features, score_families  # noqa: E402


def probe_scenario(name, iq):
    feats = extract_features(iq, fs=SAMPLE_RATE_HZ)
    scores = score_families(feats, SAMPLE_RATE_HZ)
    label, conf, _ = classify_from_features(feats, fs=SAMPLE_RATE_HZ)
    feat_names = [
        "kurtosis",
        "flatness",
        "papr_db",
        "if_entropy",
        "if_peaks",
        "hop_rate",
        "hop_prom",
        "chirp_slope",
        "chirp_resid_rms",
        "chirp_span",
        "stft_t_mean",
        "stft_t_var",
        "stft_f_mean",
        "stft_f_var",
        "peak_mean_db",
        "peak_bin_std",
    ]
    print(f"\n=== {name} ===")
    for n, v in zip(feat_names, feats, strict=False):
        print(f"  {n:>18s}: {v:.4f}")
    print(
        f"  scores: cw={scores.cw:.3f} fhss={scores.fhss:.3f} "
        f"fsk={scores.fsk:.3f} lora={scores.lora:.3f}"
    )
    print(f"  -> {label} (conf={conf:.3f})")


if __name__ == "__main__":
    rng = lambda seed: np.random.default_rng(seed)  # noqa: E731
    for snr in [20.0, 10.0, 5.0, -5.0]:
        probe_scenario(f"CW SNR={snr}", cw(rng(42), snr))
        probe_scenario(f"FHSS SNR={snr}", fhss(rng(42), snr))
        probe_scenario(f"FSK SNR={snr}", fsk(rng(42), snr))
        probe_scenario(f"LoRa SNR={snr}", lora(rng(42), snr))
    probe_scenario("Noise (no signal)", pure_noise(rng(42)))

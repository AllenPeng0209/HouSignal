#!/usr/bin/env python3
"""Reproduce the two self-created audio fixtures from their executed generator.

The numeric body below recovers the exact generator executed in this task.
Only a required output-directory CLI and byte/hash reporting were added.
No existing WAV is overwritten. No model, API, network or environment setup
is used. Generate both fixtures together: rhythm's RNG state depends on the
preceding (176400, 2) noisy-audio Gaussian draw.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import wave

import numpy as np


def generate(output_dir: Path) -> list[dict]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    filenames = ("noisy_audio.wav", "rhythm.wav")
    if any((root / name).exists() or (root / name).is_symlink() for name in filenames):
        raise FileExistsError("Use a directory without noisy_audio.wav or rhythm.wav; existing fixtures are preserved")

    sr = 44100
    rng = np.random.default_rng(20261014)
    t = np.arange(sr * 4, dtype=np.float64) / sr
    clean = np.stack((
        .18*np.sin(2*np.pi*440*t) + .05*np.sin(2*np.pi*880*t),
        .15*np.sin(2*np.pi*660*t) + .04*np.sin(2*np.pi*1320*t),
    ), axis=1)
    noise = .09*np.sin(2*np.pi*45*t)[:, None] + rng.normal(0, .035, size=clean.shape)
    noisy = np.rint(np.clip(clean + noise, -1, 1) * 32767).astype("<i2")

    length = 4.8
    rt = np.arange(round(sr * length), dtype=np.float64) / sr
    rhythm = .009*np.sin(2*np.pi*110*rt)
    onsets = [.25, .83, 1.52, 2.07, 2.89, 3.46, 4.14]
    for index, onset in enumerate(onsets):
        start = round(onset * sr)
        n = round(.16 * sr)
        local = np.arange(n) / sr
        attack = np.minimum(local / .008, 1)
        envelope = attack * np.exp(-local / .040)
        burst = .20*rng.normal(size=n) + .36*np.sin(2*np.pi*(80 + index*9)*local)
        rhythm[start:start+n] += burst * envelope
    rhythm = np.rint(np.clip(rhythm, -1, 1) * 32767).astype("<i2")

    results = []
    for filename, samples, channels in (
        ("noisy_audio.wav", noisy, 2),
        ("rhythm.wav", rhythm, 1),
    ):
        path = root / filename
        with path.open("xb") as file:
            with wave.open(file, "wb") as handle:
                handle.setnchannels(channels)
                handle.setsampwidth(2)
                handle.setframerate(sr)
                handle.writeframes(samples.tobytes())
        results.append({
            "file": filename,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "sample_rate": sr,
            "channels": channels,
            "frames": len(samples),
            "duration_seconds": len(samples) / sr,
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(generate(arguments.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

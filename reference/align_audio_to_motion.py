#!/usr/bin/env python3
"""
Align per-motion audio WAV files to SONIC reference-motion durations.

This is intended for deployment datasets where some audio clips are longer or
shorter than the corresponding motion clip. The script matches files like:

  case_test1   <->  01.wav
  case_test10  <->  10.wav

and writes a new directory of 16 kHz mono WAVs whose durations exactly match
the motion length derived from `joint_pos.csv`.

Note:
  Without ffmpeg/phase-vocoder tooling, the alignment uses linear resampling,
  which changes speaking speed and pitch. It is still much more reliable for
  synchronized deployment than stopping audio early at runtime.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
import wave
from pathlib import Path

import numpy as np


DEFAULT_MOTION_FPS = 50.0
DEFAULT_OUTPUT_SR = 16000
MAX_GAIN = 6.0
TARGET_SPEECH_RMS = 14000.0
OUTPUT_PEAK_TARGET = 0.98


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align motion audio durations to SONIC reference motions."
    )
    parser.add_argument("motion_dir", help="Directory containing motion folders")
    parser.add_argument("audio_dir", help="Directory containing source WAV files")
    parser.add_argument("output_dir", help="Directory to write aligned WAV files")
    parser.add_argument(
        "--motion-fps",
        type=float,
        default=DEFAULT_MOTION_FPS,
        help=f"Motion FPS used to compute duration (default: {DEFAULT_MOTION_FPS})",
    )
    parser.add_argument(
        "--output-sr",
        type=int,
        default=DEFAULT_OUTPUT_SR,
        help=f"Output sample rate in Hz (default: {DEFAULT_OUTPUT_SR})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite output files if they already exist",
    )
    return parser.parse_args()


def motion_number_from_name(name: str) -> int | None:
    match = re.search(r"(\d+)$", name)
    if not match:
        return None
    return int(match.group(1))


def read_motion_duration_seconds(motion_path: Path, motion_fps: float) -> tuple[int, float]:
    joint_pos_path = motion_path / "joint_pos.csv"
    if not joint_pos_path.exists():
        raise FileNotFoundError(f"Missing joint_pos.csv in {motion_path}")

    with joint_pos_path.open("r", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)  # header
        frame_count = sum(1 for _ in reader)

    if frame_count <= 0:
        raise ValueError(f"No motion frames found in {joint_pos_path}")

    duration_seconds = frame_count / motion_fps
    return frame_count, duration_seconds


def read_wave_pcm(path: Path) -> tuple[np.ndarray, int, int]:
    with wave.open(str(path), "rb") as handle:
        num_channels = handle.getnchannels()
        sample_rate = handle.getframerate()
        sample_width = handle.getsampwidth()
        num_frames = handle.getnframes()
        raw = handle.readframes(num_frames)

    if sample_width != 2:
        raise ValueError(f"Only 16-bit PCM WAV is supported: {path}")

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if num_channels > 1:
        samples = samples.reshape(-1, num_channels).mean(axis=1)

    return samples, sample_rate, num_channels


def resample_linear(samples: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
    if len(samples) == 0:
        return samples.copy()
    if source_sr == target_sr or len(samples) == 1:
        return samples.copy()

    duration = (len(samples) - 1) / source_sr
    target_len = int(round(duration * target_sr)) + 1
    source_positions = np.linspace(0.0, len(samples) - 1, num=target_len, dtype=np.float64)
    left = np.floor(source_positions).astype(np.int64)
    right = np.clip(left + 1, 0, len(samples) - 1)
    alpha = source_positions - left
    return (1.0 - alpha) * samples[left] + alpha * samples[right]


def time_stretch_to_length(samples: np.ndarray, target_len: int) -> np.ndarray:
    if target_len <= 0:
        raise ValueError(f"target_len must be positive, got {target_len}")
    if len(samples) == 0:
        return np.zeros(target_len, dtype=np.float64)
    if len(samples) == target_len:
        return samples.copy()
    if len(samples) == 1:
        return np.full(target_len, samples[0], dtype=np.float64)

    source_positions = np.linspace(0.0, len(samples) - 1, num=target_len, dtype=np.float64)
    left = np.floor(source_positions).astype(np.int64)
    right = np.clip(left + 1, 0, len(samples) - 1)
    alpha = source_positions - left
    return (1.0 - alpha) * samples[left] + alpha * samples[right]


def boost_speech_loudness(samples: np.ndarray) -> tuple[np.ndarray, float]:
    if len(samples) == 0:
        return samples.copy(), 1.0

    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < 1.0:
        return samples.copy(), 1.0

    gain = min(max(TARGET_SPEECH_RMS / rms, 1.0), MAX_GAIN)
    normalized = gain * samples / 32767.0
    limited = np.tanh(normalized)
    peak = float(np.max(np.abs(limited)))
    if peak < 1e-9:
        return samples.copy(), gain

    scaled = np.clip(limited * (OUTPUT_PEAK_TARGET / peak), -1.0, 1.0)
    return np.round(scaled * 32767.0), gain


def write_wave_pcm(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    clipped = np.clip(np.round(samples), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(clipped.tobytes())


def iter_motion_folders(motion_dir: Path) -> list[Path]:
    return sorted(path for path in motion_dir.iterdir() if path.is_dir())


def main() -> int:
    args = parse_args()
    motion_dir = Path(args.motion_dir).expanduser().resolve()
    audio_dir = Path(args.audio_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()

    if not motion_dir.is_dir():
        print(f"Error: motion_dir not found: {motion_dir}")
        return 1
    if not audio_dir.is_dir():
        print(f"Error: audio_dir not found: {audio_dir}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[tuple[str, str, int, float, float, float, float]] = []

    motion_folders = iter_motion_folders(motion_dir)
    if not motion_folders:
        print(f"Error: no motion folders found in {motion_dir}")
        return 1

    for motion_path in motion_folders:
        motion_name = motion_path.name
        motion_number = motion_number_from_name(motion_name)
        if motion_number is None:
            print(f"Skipping {motion_name}: no numeric suffix found")
            continue

        audio_path = audio_dir / f"{motion_number:02d}.wav"
        if not audio_path.exists():
            print(f"Skipping {motion_name}: missing audio {audio_path.name}")
            continue

        output_path = output_dir / audio_path.name
        if output_path.exists() and not args.force:
            print(f"Skipping existing output: {output_path}")
            continue

        frame_count, motion_duration = read_motion_duration_seconds(
            motion_path, args.motion_fps
        )
        samples, source_sr, source_channels = read_wave_pcm(audio_path)
        source_duration = len(samples) / source_sr

        resampled = resample_linear(samples, source_sr, args.output_sr)
        target_len = max(1, int(round(motion_duration * args.output_sr)))
        aligned = time_stretch_to_length(resampled, target_len)
        boosted, gain = boost_speech_loudness(aligned)
        write_wave_pcm(output_path, boosted, args.output_sr)

        output_duration = len(boosted) / args.output_sr
        stretch_ratio = motion_duration / source_duration if source_duration > 1e-9 else 1.0
        summary_rows.append(
            (
                motion_name,
                audio_path.name,
                frame_count,
                source_duration,
                motion_duration,
                output_duration,
                stretch_ratio,
            )
        )

        print(
            f"Aligned {audio_path.name} -> {output_path.name}: "
            f"audio {source_duration:.3f}s, motion {motion_duration:.3f}s, "
            f"ratio {stretch_ratio:.3f}x, gain {gain:.2f}x, "
            f"source {source_sr}Hz/{source_channels}ch -> output {args.output_sr}Hz/1ch"
        )

    summary_path = output_dir / "alignment_summary.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "motion_name",
                "audio_file",
                "motion_frames",
                "source_audio_seconds",
                "motion_seconds",
                "output_audio_seconds",
                "stretch_ratio",
            ]
        )
        writer.writerows(summary_rows)

    print(f"\nWrote aligned audio to: {output_dir}")
    print(f"Wrote summary to: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

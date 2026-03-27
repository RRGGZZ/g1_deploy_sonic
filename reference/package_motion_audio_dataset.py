#!/usr/bin/env python3
"""
Package a directory of motion pickles + wav files into SONIC deployable folders.

This is intended for datasets like:
  long_case/
    audio_cn_01-05_test.pkl
    01-05_cn.wav
    audio_en_1-5_test.pkl
    01-05_en.wav
    ...

Output:
  long_case/deploy_package/
    motions/<motion_name>/...
    audio_full_16k/<motion_name>.wav
    audio_aligned_16k/<motion_name>.wav
    package_summary.csv
    package_summary.txt
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import pickle
import re
import shutil
import wave
from pathlib import Path

import numpy as np

from convert_motions import convert_pickle_file


DEFAULT_OUTPUT_SR = 16000
DEFAULT_TARGET_FPS = 50.0
MAX_GAIN = 6.0
TARGET_SPEECH_RMS = 14000.0
OUTPUT_PEAK_TARGET = 0.98


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package a directory of motion pickles and wavs into SONIC deploy folders."
    )
    parser.add_argument("input_dir", help="Directory containing .pkl and .wav files")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output package directory (default: <input_dir>/deploy_package)",
    )
    parser.add_argument(
        "--output-sr",
        type=int,
        default=DEFAULT_OUTPUT_SR,
        help=f"Output WAV sample rate (default: {DEFAULT_OUTPUT_SR})",
    )
    parser.add_argument(
        "--target-fps",
        type=float,
        default=DEFAULT_TARGET_FPS,
        help=f"Target motion FPS (default: {DEFAULT_TARGET_FPS})",
    )
    return parser.parse_args()


def read_wave_pcm(path: Path) -> tuple[np.ndarray, int, int]:
    with contextlib.closing(wave.open(str(path), "rb")) as handle:
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
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(np.round(samples), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(clipped.tobytes())


def canonicalize_range(text: str) -> str:
    match = re.search(r"(\d+)\s*-\s*(\d+)", text)
    if not match:
        raise ValueError(f"Could not parse numeric range from: {text}")
    start = int(match.group(1))
    end = int(match.group(2))
    return f"{start:02d}-{end:02d}"


def parse_motion_identity(path: Path) -> tuple[str, str]:
    stem = path.stem.lower()
    lang_match = re.search(r"(cn|en)", stem)
    if not lang_match:
        raise ValueError(f"Could not parse language from pickle name: {path.name}")
    language = lang_match.group(1)
    numeric_range = canonicalize_range(stem)
    return language, numeric_range


def parse_audio_identity(path: Path) -> tuple[str, str]:
    stem = path.stem.lower()
    lang_match = re.search(r"(cn|en)$", stem)
    if not lang_match:
        raise ValueError(f"Could not parse language from wav name: {path.name}")
    language = lang_match.group(1)
    numeric_range = canonicalize_range(stem)
    return language, numeric_range


def read_motion_source_seconds(pkl_path: Path) -> tuple[int, float, float]:
    with pkl_path.open("rb") as handle:
        data = pickle.load(handle)
    if not isinstance(data, dict) or "dof_pos" not in data or "fps" not in data:
        raise ValueError(f"Unsupported motion pickle layout: {pkl_path}")
    frame_count = int(np.asarray(data["dof_pos"]).shape[0])
    source_fps = float(data["fps"])
    duration_seconds = frame_count / source_fps
    return frame_count, source_fps, duration_seconds


def read_motion_deploy_seconds(motion_dir: Path, target_fps: float) -> tuple[int, float]:
    joint_pos_path = motion_dir / "joint_pos.csv"
    with joint_pos_path.open("r", encoding="utf-8") as handle:
        frame_count = max(0, sum(1 for _ in handle) - 1)
    duration_seconds = frame_count / target_fps
    return frame_count, duration_seconds


def build_audio_map(input_dir: Path) -> dict[tuple[str, str], Path]:
    audio_map: dict[tuple[str, str], Path] = {}
    for wav_path in sorted(input_dir.glob("*.wav")):
        key = parse_audio_identity(wav_path)
        if key in audio_map:
            raise ValueError(f"Duplicate audio identity {key} in {input_dir}")
        audio_map[key] = wav_path
    return audio_map


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else input_dir / "deploy_package"
    )
    motions_dir = output_dir / "motions"
    audio_full_dir = output_dir / "audio_full_16k"
    audio_aligned_dir = output_dir / "audio_aligned_16k"

    pickle_paths = sorted(input_dir.glob("*.pkl"))
    if not pickle_paths:
        raise ValueError(f"No .pkl files found in {input_dir}")
    audio_map = build_audio_map(input_dir)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, object]] = []

    for pkl_path in pickle_paths:
        motion_name = pkl_path.stem
        language, numeric_range = parse_motion_identity(pkl_path)
        audio_key = (language, numeric_range)
        if audio_key not in audio_map:
            raise FileNotFoundError(
                f"No wav matched {pkl_path.name} with identity {audio_key}"
            )
        wav_path = audio_map[audio_key]

        print(f"\nPackaging motion: {motion_name}")
        print(f"  matched audio: {wav_path.name}")

        convert_pickle_file(
            pickle_path=pkl_path,
            explicit_output_dir=motions_dir,
            target_fps=args.target_fps,
        )

        motion_dir = motions_dir / motion_name
        if not motion_dir.is_dir():
            raise FileNotFoundError(f"Expected converted motion folder: {motion_dir}")

        motion_source_frames, motion_source_fps, motion_source_seconds = read_motion_source_seconds(
            pkl_path
        )
        motion_deploy_frames, motion_deploy_seconds = read_motion_deploy_seconds(
            motion_dir, args.target_fps
        )
        source_samples, source_sr, source_channels = read_wave_pcm(wav_path)
        source_audio_seconds = len(source_samples) / source_sr
        resampled = resample_linear(source_samples, source_sr, args.output_sr)
        boosted_full, full_gain = boost_speech_loudness(resampled)
        aligned_target_len = max(1, int(round(motion_deploy_seconds * args.output_sr)))
        aligned = time_stretch_to_length(resampled, aligned_target_len)
        boosted_aligned, aligned_gain = boost_speech_loudness(aligned)

        write_wave_pcm(audio_full_dir / f"{motion_name}.wav", boosted_full, args.output_sr)
        write_wave_pcm(
            audio_aligned_dir / f"{motion_name}.wav",
            boosted_aligned,
            args.output_sr,
        )

        summary_rows.append(
            {
                "motion_name": motion_name,
                "language": language,
                "range": numeric_range,
                "source_pickle": pkl_path.name,
                "source_wav": wav_path.name,
                "motion_frames_source": motion_source_frames,
                "motion_fps_source": motion_source_fps,
                "motion_seconds_source": motion_source_seconds,
                "motion_frames_deploy": motion_deploy_frames,
                "motion_seconds_deploy": motion_deploy_seconds,
                "audio_seconds_source": source_audio_seconds,
                "audio_full_seconds": len(boosted_full) / args.output_sr,
                "audio_aligned_seconds": len(boosted_aligned) / args.output_sr,
                "audio_full_gain": full_gain,
                "audio_aligned_gain": aligned_gain,
            }
        )

    summary_csv = output_dir / "package_summary.csv"
    with summary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    summary_txt = output_dir / "package_summary.txt"
    deploy_root = Path(__file__).resolve().parents[1]
    motions_rel = motions_dir.relative_to(deploy_root)
    audio_full_rel = audio_full_dir.relative_to(deploy_root)
    audio_aligned_rel = audio_aligned_dir.relative_to(deploy_root)
    with summary_txt.open("w", encoding="utf-8") as handle:
        handle.write("Motion Audio Dataset Deploy Package\n")
        handle.write("==================================\n\n")
        handle.write(f"input_dir: {input_dir}\n")
        handle.write(f"output_dir: {output_dir}\n")
        handle.write(f"motions: {len(summary_rows)}\n\n")
        for row in summary_rows:
            handle.write(f"{row['motion_name']}:\n")
            handle.write(f"  language: {row['language']}\n")
            handle.write(f"  range: {row['range']}\n")
            handle.write(f"  source_pickle: {row['source_pickle']}\n")
            handle.write(f"  source_wav: {row['source_wav']}\n")
            handle.write(
                f"  motion_seconds_deploy: {float(row['motion_seconds_deploy']):.3f}\n"
            )
            handle.write(
                f"  audio_full_seconds: {float(row['audio_full_seconds']):.3f}\n"
            )
            handle.write(
                f"  audio_aligned_seconds: {float(row['audio_aligned_seconds']):.3f}\n"
            )
            handle.write("\n")
        handle.write("Suggested deploy command (full audio):\n")
        handle.write(
            f"  ./deploy.sh --motion-data {motions_rel} "
            f"--motion-audio {audio_full_rel} real\n\n"
        )
        handle.write("Suggested deploy command (aligned audio):\n")
        handle.write(
            f"  ./deploy.sh --motion-data {motions_rel} "
            f"--motion-audio {audio_aligned_rel} real\n"
        )

    print("\nPackage created successfully:")
    print(f"  motions:          {motions_dir}")
    print(f"  audio_full_16k:   {audio_full_dir}")
    print(f"  audio_aligned_16k:{audio_aligned_dir}")
    print(f"  summary:          {summary_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

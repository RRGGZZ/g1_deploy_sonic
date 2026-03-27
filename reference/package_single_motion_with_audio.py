#!/usr/bin/env python3
"""
Package a single motion pickle + wav into SONIC deployable folders.

Input example:
  qwen_audio_new/
    audio_test.pkl
    audio.wav

Output example:
  qwen_audio_new/deploy_package/
    motions/audio_test/...
    audio_full_16k/audio_test.wav
    audio_aligned_16k/audio_test.wav
    package_summary.txt
"""

from __future__ import annotations

import argparse
import contextlib
import pickle
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
        description="Package a single motion pickle and WAV into SONIC deploy folders."
    )
    parser.add_argument("input_dir", help="Directory containing one .pkl and one .wav")
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


def find_single_file(input_dir: Path, suffix: str) -> Path:
    matches = sorted(path for path in input_dir.iterdir() if path.suffix.lower() == suffix)
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one '{suffix}' file in {input_dir}, found {len(matches)}"
        )
    return matches[0]


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


def read_motion_duration_seconds_from_pkl(path: Path) -> tuple[str, int, float, float]:
    with path.open("rb") as handle:
        data = pickle.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict in pickle: {path}")
    if "dof_pos" not in data or "fps" not in data:
        raise ValueError(f"Missing required keys in pickle: {path}")
    motion_name = path.stem
    frame_count = int(np.asarray(data["dof_pos"]).shape[0])
    source_fps = float(data["fps"])
    motion_seconds = frame_count / source_fps
    return motion_name, frame_count, source_fps, motion_seconds


def maybe_rename_single_motion_folder(motions_dir: Path, target_name: str) -> Path:
    subdirs = sorted(path for path in motions_dir.iterdir() if path.is_dir())
    if len(subdirs) != 1:
        raise ValueError(f"Expected one motion folder under {motions_dir}, found {len(subdirs)}")
    current = subdirs[0]
    target = motions_dir / target_name
    if current == target:
        return target
    if target.exists():
        shutil.rmtree(target)
    current.rename(target)
    return target


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

    pkl_path = find_single_file(input_dir, ".pkl")
    wav_path = find_single_file(input_dir, ".wav")

    motion_name, frame_count, source_fps, motion_seconds = read_motion_duration_seconds_from_pkl(
        pkl_path
    )
    print(f"Motion pickle: {pkl_path.name}")
    print(f"Audio wav:     {wav_path.name}")
    print(
        f"Motion info:   {frame_count} frames @ {source_fps:.6f} fps "
        f"({motion_seconds:.3f} s), deploy target {args.target_fps:.1f} fps"
    )

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    convert_pickle_file(
        pickle_path=pkl_path,
        explicit_output_dir=motions_dir,
        target_fps=args.target_fps,
    )
    motion_folder = maybe_rename_single_motion_folder(motions_dir, motion_name)

    with (motion_folder / "joint_pos.csv").open("r", encoding="utf-8") as handle:
        motion_frame_count = max(0, sum(1 for _ in handle) - 1)
    motion_duration_deploy = motion_frame_count / args.target_fps

    source_samples, source_sr, source_channels = read_wave_pcm(wav_path)
    source_duration = len(source_samples) / source_sr
    resampled = resample_linear(source_samples, source_sr, args.output_sr)
    boosted_full, full_gain = boost_speech_loudness(resampled)
    aligned_target_len = max(1, int(round(motion_duration_deploy * args.output_sr)))
    aligned = time_stretch_to_length(resampled, aligned_target_len)
    boosted_aligned, aligned_gain = boost_speech_loudness(aligned)

    full_output = audio_full_dir / f"{motion_name}.wav"
    aligned_output = audio_aligned_dir / f"{motion_name}.wav"
    write_wave_pcm(full_output, boosted_full, args.output_sr)
    write_wave_pcm(aligned_output, boosted_aligned, args.output_sr)

    summary_path = output_dir / "package_summary.txt"
    deploy_root = Path(__file__).resolve().parents[1]
    motions_rel = motions_dir.relative_to(deploy_root)
    audio_full_rel = audio_full_dir.relative_to(deploy_root)
    audio_aligned_rel = audio_aligned_dir.relative_to(deploy_root)
    with summary_path.open("w", encoding="utf-8") as handle:
        handle.write("Single Motion Deploy Package\n")
        handle.write("===========================\n\n")
        handle.write(f"input_dir: {input_dir}\n")
        handle.write(f"motion_name: {motion_name}\n")
        handle.write(f"motion_pickle: {pkl_path.name}\n")
        handle.write(f"audio_wav: {wav_path.name}\n")
        handle.write(f"motion_frames_source: {frame_count}\n")
        handle.write(f"motion_fps_source: {source_fps:.6f}\n")
        handle.write(f"motion_frames_deploy: {motion_frame_count}\n")
        handle.write(f"motion_fps_deploy: {args.target_fps:.1f}\n")
        handle.write(f"motion_seconds_deploy: {motion_duration_deploy:.3f}\n")
        handle.write(f"audio_seconds_source: {source_duration:.3f}\n")
        handle.write(f"audio_output_sr: {args.output_sr}\n")
        handle.write(f"audio_full_gain: {full_gain:.3f}\n")
        handle.write(f"audio_aligned_gain: {aligned_gain:.3f}\n")
        handle.write("\nGenerated paths:\n")
        handle.write(f"  motions: {motions_dir}\n")
        handle.write(f"  audio_full_16k: {audio_full_dir}\n")
        handle.write(f"  audio_aligned_16k: {audio_aligned_dir}\n")
        handle.write("\nSuggested deploy command (full audio):\n")
        handle.write(
            f"  ./deploy.sh --motion-data {motions_rel} "
            f"--motion-audio {audio_full_rel} real\n"
        )
        handle.write("\nSuggested deploy command (aligned audio):\n")
        handle.write(
            f"  ./deploy.sh --motion-data {motions_rel} "
            f"--motion-audio {audio_aligned_rel} real\n"
        )

    print("\nPackage created successfully:")
    print(f"  motions:          {motions_dir}")
    print(f"  audio_full_16k:   {audio_full_dir}")
    print(f"  audio_aligned_16k:{audio_aligned_dir}")
    print(f"  summary:          {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

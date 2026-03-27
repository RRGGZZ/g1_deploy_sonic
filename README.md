# g1_deploy_sonic

`g1_deploy_sonic` is a standalone deployment repository for Unitree G1 whole-body motion playback with synchronized audio.

This repo packages deployment code, model/runtime assets, scripts, and reference datasets so users can run it directly after `git clone`, without extra override/plugin integration from another monorepo.

## What This Project Does

This project is used to deploy pre-generated motion sequences to a real Unitree G1 robot (or compatible runtime path), while optionally playing corresponding speech/audio in sync.

Typical use case:

1. Prepare a motion package (`motions`) and optional audio package (`audio_full_16k`).
2. Start deployment in `real` mode.
3. Select and trigger playback through keyboard control.
4. Robot executes motion sequence and audio thread streams synchronized audio.

## Key Features

- Standalone deployment workflow (no external override required).
- Motion and audio co-playback for showcase/demo scenarios.
- Built-in runtime controls (`n`, `t`, `o`) for operation during execution.
- Packaged example data under `reference/example_scalelab/`.
- Tail-truncation fixes in audio pipeline (resampling and stop/drain behavior improvements).

## Repository Layout

- `src/`: core C++ deployment runtime implementation.
- `deploy.sh`: main entry script for real deployment run.
- `reference/`: example deployment packages and tutorial assets.
- `data/`: processed deployment data and helper assets.
- `thirdparty/`: third-party SDK/runtime dependencies.
- `build/`: CMake build output directory (generated locally).

## Quick Start

### 1) Clone

```bash
git clone https://github.com/RRGGZZ/g1_deploy_sonic.git
cd g1_deploy_sonic
```

### 2) Build

Build in Docker or in your prepared native environment:

```bash
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . -j
```

### 3) Run a Real-Robot Example

From repo root:

```bash
./deploy.sh real \
  --motion-data reference/example_scalelab/long_case/deploy_package/motions \
  --motion-audio-dir reference/example_scalelab/long_case/deploy_package/audio_full_16k
```

## Runtime Controls

- `n`: switch to next motion clip.
- `t`: start playback of currently selected motion.
- `o`: emergency stop.

Recommended operation:

1. Start program.
2. Press `n` until target clip is selected.
3. Press `t` to execute.
4. Use `o` immediately if emergency stop is needed.

## Data Expectations

When running with:

```bash
--motion-data <path_to_motions>
--motion-audio-dir <path_to_audio>
```

the deployment runtime expects:

- motion files in the `motions` directory (processed package format used by this repo),
- matching audio files in the audio directory,
- naming/order consistency between motion list and audio list for proper synchronization.

If audio is not needed, run with motion-only options supported by your current scripts/config.

## Notes on Audio Synchronization

This repository includes fixes for previously observed audio tail truncation during real playback:

- improved stop grace/drain timing,
- endpoint-aware resampling behavior,
- natural-completion handling to avoid hard stop at normal finish.

If you still see clipping, verify:

- input audio sample rate/path is correct,
- motion/audio pair indexing is consistent,
- runtime is not interrupted by manual stop/switch actions.

## Troubleshooting

- Build fails:
  - verify toolchain and third-party dependencies are installed,
  - rebuild from clean `build/` directory.
- Robot does not move:
  - check robot connection/state and runtime mode,
  - confirm motion package path is valid.
- No audio or mismatched audio:
  - verify `--motion-audio-dir` path,
  - check naming/order between motion clips and audio files.

## Safety Reminder

Running in `real` mode controls a physical robot. Always keep an emergency stop path available and test with safe posture/environment first.

## Misc

- Default ignored paths include `build/`, `target/`, `logs/`, and `*.trt`.
- `main` branch is intended to stay directly usable for clone-build-run workflow.
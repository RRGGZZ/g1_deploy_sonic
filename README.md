# g1_deploy_sonic

`g1_deploy_sonic` is a standalone deployment workspace for Unitree G1 based on SONIC.

This repository is intended to be used directly after `git clone`, without any external override workflow.

## Quick Start

1. Clone:

```bash
git clone https://github.com/RRGGZZ/g1_deploy_sonic.git
cd g1_deploy_sonic
```

2. Build (inside Docker or your prepared native environment):

```bash
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . -j
```

3. Run deployment example:

```bash
./deploy.sh real \
  --motion-data reference/example_scalelab/long_case/deploy_package/motions \
  --motion-audio-dir reference/example_scalelab/long_case/deploy_package/audio_full_16k
```

## Runtime Controls

- `n`: switch to next motion
- `t`: start playback
- `o`: emergency stop

Before starting, press `n` to select the target motion, then press `t`.

## Notes

- Audio playback timing/alignment improvements are already included in this repo.
- `build/`, `target/`, `logs/`, and `*.trt` are ignored by default.
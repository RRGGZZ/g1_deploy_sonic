# g1_deploy_sonic

Overlay-style plugin repo for adding motion-audio deployment support to the
original SONIC / `gear_sonic_deploy` codebase.

This repo is intentionally small:
- it does not vendor the full SONIC source tree
- it only ships the modified files needed for:
  - ScaleLab / GMR motion conversion
  - motion-synced robot speaker playback
  - offline audio alignment to motion duration
  - standalone robot-speaker audio debugging

## Install Into An Existing SONIC Repo

Clone the original SONIC repo first, then clone this repo inside it:

```bash
git clone <original-sonic-url> sonic
cd sonic
git clone https://github.com/RRGGZZ/g1_deploy_sonic.git plugins/g1_deploy_sonic
bash plugins/g1_deploy_sonic/install.sh .
```

## Uninstall

```bash
bash plugins/g1_deploy_sonic/uninstall.sh .
```

## Generate Motions

```bash
cd gear_sonic_deploy
python reference/convert_motions.py \
  reference/example_scalelab/qwen_audio/g1_pkl_en \
  reference/example_scalelab/qwen_audio/g1_ref_en \
  --target-fps 50
```

## Align Audio To Motion Duration

```bash
python reference/align_audio_to_motion.py \
  reference/example_scalelab/qwen_audio/g1_ref_en \
  reference/example_scalelab/qwen_audio/audio_en \
  reference/example_scalelab/qwen_audio/audio_aligned_16k_en \
  --motion-fps 50 \
  --output-sr 16000 \
  --force
```

## Build

```bash
cmake -S . -B build
cmake --build build --target g1_deploy_onnx_ref g1_audio_debug_player -j4
```

## Debug Robot Speaker Only

Use the robot NIC, for example `enp8s0`:

```bash
./target/release/g1_audio_debug_player \
  enp8s0 \
  reference/example_scalelab/qwen_audio/audio_aligned_16k_en/02.wav
```

## Deploy

```bash
./deploy.sh \
  --motion-data reference/example_scalelab/qwen_audio/g1_ref_en \
  --motion-audio reference/example_scalelab/qwen_audio/audio_aligned_16k_en \
  real
```

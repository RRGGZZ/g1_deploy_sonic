# g1_deploy_sonic

This repo is an overlay plugin for the original SONIC `gear_sonic_deploy`.

It includes:
- the modified deployment source files
- `gear_sonic_deploy/visualize_motion.py`
- the `example_scalelab` demo dataset
- helper scripts so a fresh machine can install and run with one command

## What This Plugin Adds

- ScaleLab / GMR `.pkl` to SONIC motion conversion
- corrected joint remapping for G1 arm motion
- offline audio-to-motion alignment
- robot speaker playback that follows motion switching more robustly
- standalone G1 speaker debug player
- built-in visualization script with no hard dependency on `lxml`

## Repo Layout

```text
plugins/g1_deploy_sonic/
├── overlay/                     # files copied into SONIC
├── data/example_scalelab/       # demo dataset shipped with this repo
├── install.sh                   # install overlay + dataset link
├── uninstall.sh                 # remove overlay changes
└── quickstart.sh                # one-command entrypoint
```

`install.sh` copies the overlay files into `gear_sonic_deploy/` and creates:

```text
gear_sonic_deploy/reference/example_scalelab -> plugins/g1_deploy_sonic/data/example_scalelab
```

So after installation, the motion/audio paths stay exactly the same as your local workflow.

## Clone And Install

Clone the original SONIC repo first, then clone this plugin into it:

```bash
git clone <original-sonic-url> sonic
cd sonic
git clone https://github.com/RRGGZZ/g1_deploy_sonic.git plugins/g1_deploy_sonic
bash plugins/g1_deploy_sonic/quickstart.sh . install
```

## One-Command Usage

Visualize the demo motion:

```bash
bash plugins/g1_deploy_sonic/quickstart.sh . visualize
```

Convert the shipped raw `.pkl` motions:

```bash
bash plugins/g1_deploy_sonic/quickstart.sh . convert
```

Align the shipped audio to motion duration:

```bash
bash plugins/g1_deploy_sonic/quickstart.sh . align
```

Build the deploy binaries:

```bash
bash plugins/g1_deploy_sonic/quickstart.sh . build
```

Deploy on the real robot:

```bash
bash plugins/g1_deploy_sonic/quickstart.sh . deploy real
```

If you already know the robot NIC, you can pass it directly:

```bash
bash plugins/g1_deploy_sonic/quickstart.sh . deploy enp8s0
```

## Manual Commands

After installation, you can also use the normal `gear_sonic_deploy` commands directly.

Convert motions:

```bash
cd gear_sonic_deploy
python reference/convert_motions.py \
  reference/example_scalelab/qwen_audio/g1_pkl_en \
  reference/example_scalelab/qwen_audio/g1_ref_en \
  --target-fps 50
```

Visualize a motion:

```bash
python visualize_motion.py \
  --motion_dir reference/example_scalelab/qwen_50fps/case_test01
```

Align audio:

```bash
python reference/align_audio_to_motion.py \
  reference/example_scalelab/qwen_audio/g1_ref_en \
  reference/example_scalelab/qwen_audio/audio_en \
  reference/example_scalelab/qwen_audio/audio_aligned_16k_en \
  --motion-fps 50 \
  --output-sr 16000 \
  --force
```

Build manually:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build -j"$(nproc)"
```

Debug robot speaker only:

```bash
./target/release/g1_audio_debug_player \
  enp8s0 \
  reference/example_scalelab/qwen_audio/audio_aligned_16k_en/02.wav
```

Deploy manually:

```bash
./deploy.sh \
  --motion-data reference/example_scalelab/qwen_audio/g1_ref_en \
  --motion-audio reference/example_scalelab/qwen_audio/audio_aligned_16k_en \
  real
```

## Python Packages For Visualization / Conversion

Typical Python dependencies:

```bash
pip install numpy scipy mujoco pyzmq msgpack
```

`lxml` is optional now. If it is installed, `visualize_motion.py` will use it. If not, it falls back to Python's standard XML library.

## Uninstall

```bash
bash plugins/g1_deploy_sonic/uninstall.sh .
```

#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ $# -gt 0 ] && [ -d "$1" ] && [ -d "$1/gear_sonic_deploy" ]; then
  SONIC_ROOT="$(cd "$1" && pwd)"
  shift
else
  SONIC_ROOT="$(pwd)"
fi

COMMAND="${1:-help}"
if [ $# -gt 0 ]; then
  shift
fi

usage() {
  cat <<EOF
Usage:
  bash plugins/g1_deploy_sonic/quickstart.sh [SONIC_ROOT] <command> [args...]

Commands:
  install
  build
  convert
  package-new
  align
  visualize [motion_dir]
  deploy [real|sim|<interface>|<ip>]
  deploy-new [real|sim|<interface>|<ip>]

Examples:
  bash plugins/g1_deploy_sonic/quickstart.sh . install
  bash plugins/g1_deploy_sonic/quickstart.sh . visualize
  bash plugins/g1_deploy_sonic/quickstart.sh . build
  bash plugins/g1_deploy_sonic/quickstart.sh . deploy real
  bash plugins/g1_deploy_sonic/quickstart.sh . package-new
  bash plugins/g1_deploy_sonic/quickstart.sh . deploy-new real
EOF
}

ensure_sonic_root() {
  if [ ! -d "$SONIC_ROOT/gear_sonic_deploy" ]; then
    echo "Error: $SONIC_ROOT does not look like the SONIC repo root"
    exit 1
  fi
}

run_install() {
  bash "$PLUGIN_ROOT/install.sh" "$SONIC_ROOT"
}

build_release() {
  (
    cd "$SONIC_ROOT/gear_sonic_deploy"
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
    cmake --build build -j"$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"
  )
}

case "$COMMAND" in
  install)
    ensure_sonic_root
    run_install
    ;;
  build)
    ensure_sonic_root
    run_install
    build_release
    ;;
  convert)
    ensure_sonic_root
    run_install
    (
      cd "$SONIC_ROOT/gear_sonic_deploy"
      python reference/convert_motions.py \
        reference/example_scalelab/qwen_audio/g1_pkl_en \
        reference/example_scalelab/qwen_audio/g1_ref_en \
        --target-fps 50
    )
    ;;
  package-new)
    ensure_sonic_root
    run_install
    (
      cd "$SONIC_ROOT/gear_sonic_deploy"
      python reference/package_single_motion_with_audio.py \
        reference/example_scalelab/qwen_audio_new
    )
    ;;
  align)
    ensure_sonic_root
    run_install
    (
      cd "$SONIC_ROOT/gear_sonic_deploy"
      python reference/align_audio_to_motion.py \
        reference/example_scalelab/qwen_audio/g1_ref_en \
        reference/example_scalelab/qwen_audio/audio_en \
        reference/example_scalelab/qwen_audio/audio_aligned_16k_en \
        --motion-fps 50 \
        --output-sr 16000 \
        --force
    )
    ;;
  visualize)
    ensure_sonic_root
    run_install
    MOTION_DIR="${1:-reference/example_scalelab/qwen_50fps/case_test01}"
    (
      cd "$SONIC_ROOT/gear_sonic_deploy"
      python visualize_motion.py --motion_dir "$MOTION_DIR"
    )
    ;;
  deploy)
    ensure_sonic_root
    run_install
    build_release
    TARGET="${1:-real}"
    (
      cd "$SONIC_ROOT/gear_sonic_deploy"
      ./deploy.sh \
        --motion-data reference/example_scalelab/qwen_audio/g1_ref_en \
        --motion-audio reference/example_scalelab/qwen_audio/audio_aligned_16k_en \
        "$TARGET"
    )
    ;;
  deploy-new)
    ensure_sonic_root
    run_install
    (
      cd "$SONIC_ROOT/gear_sonic_deploy"
      python reference/package_single_motion_with_audio.py \
        reference/example_scalelab/qwen_audio_new
    )
    build_release
    TARGET="${1:-real}"
    (
      cd "$SONIC_ROOT/gear_sonic_deploy"
      ./deploy.sh \
        --motion-data reference/example_scalelab/qwen_audio_new/deploy_package/motions \
        --motion-audio reference/example_scalelab/qwen_audio_new/deploy_package/audio_full_16k \
        "$TARGET"
    )
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $COMMAND"
    echo ""
    usage
    exit 1
    ;;
esac

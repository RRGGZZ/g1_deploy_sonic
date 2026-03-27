#!/bin/bash
# 快速部署脚本 - qwen_audio_new 真机部署
# Usage: ./deploy_qwen_audio_new.sh [real|sim|<interface>]

set -e

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && cd ../../.. && pwd)"
MOTION_DATA_DIR="$DEPLOY_DIR/reference/example_scalelab/qwen_audio_new"
AUDIO_DIR="$MOTION_DATA_DIR/audio_aligned_16k_en"

# 默认为 real
DEPLOY_MODE="${1:-real}"

echo "========================================="
echo "  qwen_audio_new 真机部署脚本"
echo "========================================="
echo ""
echo "部署模式: $DEPLOY_MODE"
echo "动作文件: $MOTION_DATA_DIR"
echo "音频文件: $AUDIO_DIR"
echo ""

# 验证文件
if [ ! -d "$MOTION_DATA_DIR/audio_test" ]; then
    echo "❌ 错误: 找不到转化后的动作文件"
    echo "   预期位置: $MOTION_DATA_DIR/audio_test"
    exit 1
fi

if [ ! -f "$AUDIO_DIR/01.wav" ]; then
    echo "❌ 错误: 找不到音频文件"
    echo "   预期位置: $AUDIO_DIR/01.wav"
    exit 1
fi

echo "✓ 文件验证成功"
echo ""

# 进入部署目录
cd "$DEPLOY_DIR"

echo "正在启动部署..."
echo ""

# 执行部署
./deploy.sh "$DEPLOY_MODE" \
    --motion-data "$MOTION_DATA_DIR" \
    --motion-audio-dir "$AUDIO_DIR"

echo ""
echo "========================================="
echo "  部署完成！"
echo "========================================="
echo ""
echo "运行时快捷键:"
echo "  T - 播放动作"
echo "  N - 切换下一个动作"
echo "  P - 暂停/继续"
echo "  R - 重新启动"
echo "  Q - 退出"
echo ""

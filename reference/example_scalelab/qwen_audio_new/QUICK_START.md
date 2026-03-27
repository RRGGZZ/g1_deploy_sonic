# 🚀 qwen_audio_new 部署快速参考

## 📋 转化状态
✅ **完成** - 已成功将 `audio_test.pkl` 转化为 SONIC 部署格式

### 转化详情
| 项目 | 值 |
|------|-----|
| 动作名称 | `audio_test` |
| 总帧数 | 3012 |
| 关节数 | 29 |
| 采样率 | 50 FPS |
| 总时长 | 约 60 秒 |
| 源格式 | ScaleLab/GMR |

## 📁 文件结构（已就绪）

```
✓ audio_test/              - 转化后的动作 CSV 文件
✓ audio_aligned_16k_en/    - 16kHz 采样的音频文件
✓ motion_summary.txt       - 转化摘要
✓ DEPLOYMENT_GUIDE.md      - 详细部署指南
✓ deploy_qwen_audio_new.sh - 快速部署脚本
```

## ⚡ 快速部署命令

### 方法 1️⃣ - 使用快速脚本（最简单）
```bash
cd /home/r/Downloads/GR00T-WholeBodyControl/gear_sonic_deploy/reference/example_scalelab/qwen_audio_new
./deploy_qwen_audio_new.sh real
```

### 方法 2️⃣ - 从项目根目录（标准方式）
```bash
cd /home/r/Downloads/GR00T-WholeBodyControl/gear_sonic_deploy

# 自动检测网络部署
./deploy.sh real \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

### 方法 3️⃣ - 仿真测试（部署前测试）
```bash
cd /home/r/Downloads/GR00T-WholeBodyControl/gear_sonic_deploy

./deploy.sh sim \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

## 🎯 部署参数说明

```
./deploy.sh [MODE] [OPTIONS]

MODE 选择:
  real              自动检测机器人网络接口（推荐）
  sim               仿真模式 (MuJoCo 本地测试)
  eth0              指定网络接口（替换为你的接口）
  192.168.x.x       指定IP地址

OPTIONS:
  --motion-data <dir>      动作 CSV 文件目录
  --motion-audio-dir <dir> 音频 WAV 文件目录
```

## 🎮 运行时快捷键

启动后使用以下键盘快捷键控制：

| 键 | 功能 |
|----|------|
| **T** | 📍 播放/三角波 当前动作 |
| **N** | ⏭️ 切换到下一个动作 |
| **P** | ⏸️ 暂停/继续播放 |
| **R** | 🔄 重新启动当前动作 |
| **Q** | ❌ 退出程序 |

## ✅ 预部署检查清单

- [ ] 机器人已连接到网络
- [ ] 网络接口正确（查看：`ip addr show`）
- [ ] 所有 CSV 文件在 `audio_test/` 目录
- [ ] 音频文件在 `audio_aligned_16k_en/01.wav`
- [ ] 可选：先在仿真模式测试一次

## 🔧 常见问题

### ❓ 如何检查机器人网络？
```bash
ping 192.168.123.161
```

### ❓ 如何查看可用网络接口？
```bash
ip addr show
```

### ❓ 如何测试动作在仿真中是否正确？
```bash
cd /home/r/Downloads/GR00T-WholeBodyControl/gear_sonic_deploy
./deploy.sh sim \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

### ❓ 音频没有播放？
1. 检查 `audio_aligned_16k_en/01.wav` 存在
2. 检查机器人音量设置
3. 确认音频格式为 16kHz WAV 单通道

### ❓ 动作播放不流畅？
1. 重新启动机器人
2. 确认网络连接稳定
3. 尝试仿真模式排查

## 📚 详细文档

详见 [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)

## 📞 关键文件位置

```
/home/r/Downloads/GR00T-WholeBodyControl/
├── gear_sonic_deploy/
│   ├── deploy.sh                          ← 主部署脚本
│   └── reference/example_scalelab/qwen_audio_new/
│       ├── audio_test/                    ← 转化后的动作数据 ✓
│       ├── audio_aligned_16k_en/          ← 音频文件 ✓
│       ├── deploy_qwen_audio_new.sh       ← 快速部署脚本 ✓
│       ├── DEPLOYMENT_GUIDE.md            ← 完整指南 ✓
│       └── QUICK_START.md                 ← 本文件 ✓
```

---

✨ **准备就绪！你可以立即部署到真机。** ✨

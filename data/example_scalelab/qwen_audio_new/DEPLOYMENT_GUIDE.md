# qwen_audio_new 真机部署指南

## 📁 文件结构（已转化完成）

```
qwen_audio_new/
├── audio.wav                          # 原始音频文件
├── audio_test.pkl                     # 原始动作pickle文件
├── audio_test/                        # 转化后的动作数据
│   ├── body_pos_w.csv                 # 身体位置
│   ├── body_quat_w.csv                # 身体四元数
│   ├── joint_pos.csv                  # 关节位置
│   ├── joint_vel.csv                  # 关节速度
│   ├── motion_info.txt                # 动作信息
│   └── body_ang_vel_w.csv             # 身体角速度
├── audio_aligned_16k_en/              # 16k采样率的对齐音频
│   └── 01.wav                         # 匹配动作的音频
├── motion_summary.txt                 # 动作转化总结
└── DEPLOYMENT_GUIDE.md                # 本文件
```

## ✅ 验证文件完整性

请检查以下文件是否存在：

```bash
# 检查转化后的动作文件
ls -l audio_test/
# 应该包含: body_pos_w.csv, body_quat_w.csv, joint_pos.csv, joint_vel.csv

# 检查音频文件
ls -l audio_aligned_16k_en/
# 应该包含: 01.wav

# 查看动作摘要
cat motion_summary.txt
```

## 🚀 真机部署命令

### 1. 进入部署目录
```bash
cd /home/r/Downloads/GR00T-WholeBodyControl/gear_sonic_deploy
```

### 2. 构建项目（如果还未构建）
```bash
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j4
cd ..
```

### 3. 部署到真机（最重要！）

**选项A：自动检测网络（推荐）**
```bash
./deploy.sh real \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

**选项B：指定网络接口**
```bash
./deploy.sh eth0 \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

**选项C：指定IP地址**
```bash
./deploy.sh 192.168.123.161 \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

### 4. 仿真测试（在测试前可选）
```bash
./deploy.sh sim \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

## 🎮 运行时控制

部署启动后，使用以下键盘快捷键：

- **T**: 播放当前动作
- **N**: 切换下一个动作
- **P**: 暂停/继续
- **R**: 重新启动当前动作
- **Q**: 退出程序

## 📊 动作信息

```
动作名称: audio_test
总帧数: 3012
关节数: 29
采样率: 50 FPS （约60秒）
```

## 🔧 故障排除

### 连接失败
1. 确认机器人网络连接正常：`ping 192.168.123.161`
2. 确认网络接口正确：`ip addr show`

### 音频未播放
1. 确认音频文件存在：`ls audio_aligned_16k_en/`
2. 确认音频格式为 WAV 16kHz 单通道
3. 检查机器人音量设置

### 动作播放异常
1. 在仿真模式下先测试：`./deploy.sh sim ...`
2. 检查转化是否成功：`cat motion_summary.txt`
3. 可视化动作：`python visualize_motion.py --motion_dir audio_test`

## 📝 详细部署参数说明

| 参数 | 说明 | 示例 |
|------|------|------|
| `real` | 自动检测机器人网络 | - |
| `sim` | 仿真模式（本地测试） | - |
| `eth0` | 指定网络接口 | - |
| `--motion-data` | 动作数据目录 | `reference/example_scalelab/qwen_audio_new` |
| `--motion-audio-dir` | 音频文件目录 | `reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en` |

## ✨ 完整部署命令模板

真机部署（完整版）：
```bash
./deploy.sh real \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

仿真测试（完整版）：
```bash
./deploy.sh sim \
  --motion-data reference/example_scalelab/qwen_audio_new \
  --motion-audio-dir reference/example_scalelab/qwen_audio_new/audio_aligned_16k_en
```

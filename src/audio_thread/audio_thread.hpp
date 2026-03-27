#pragma once

#include <chrono>
#include <filesystem>
#include <memory>
#include <mutex>
#include <optional>
#include <stop_token>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <unitree/robot/g1/audio/g1_audio_client.hpp>

struct AudioCommand {
  bool streaming_data_absent = false;
  bool motion_audio_enabled = false;
  bool motion_playing = false;
  int current_frame = 0;
  std::string motion_name;
};

class AudioThread {
 public:
  explicit AudioThread(const std::string& motion_audio_dir = "");
  ~AudioThread();

  void SetCommand(const AudioCommand& command);

 private:
  static constexpr int kTargetSampleRate = 16000;

  struct CachedAudioClip {
    std::vector<uint8_t> pcm_bytes_16khz_mono;
    int sample_rate = 0;
    int num_channels = 0;
  };

  void loop(std::stop_token st);
  void StartMotionPlayback(const std::string& motion_name);
  void StopMotionPlayback();
  void PlaybackMotionAudio(std::stop_token st, std::string motion_name, std::filesystem::path audio_path);

  std::optional<std::filesystem::path> ResolveAudioPathForMotion(const std::string& motion_name);
  std::optional<CachedAudioClip> GetOrLoadAudioClip(const std::filesystem::path& audio_path);
  std::optional<CachedAudioClip> LoadAudioClip(const std::filesystem::path& audio_path) const;

  static std::vector<std::string> BuildAudioFileCandidates(const std::string& motion_name);
  static std::vector<int16_t> DecodePcm16(const std::vector<uint8_t>& pcm_bytes);
  static std::vector<int16_t> DownmixToMono(const std::vector<int16_t>& samples, int num_channels);
  static std::vector<int16_t> ResampleLinear(const std::vector<int16_t>& samples, int source_sample_rate, int target_sample_rate);
  static std::vector<uint8_t> EncodePcm16(const std::vector<int16_t>& samples);

  unitree::robot::g1::AudioClient client_;
  std::jthread thread_;
  std::jthread playback_thread_;

  std::mutex command_mutex_;
  AudioCommand command_;
  AudioCommand command_last_;

  std::mutex client_mutex_;
  std::mutex playback_mutex_;
  std::mutex cache_mutex_;

  std::filesystem::path motion_audio_dir_;
  bool motion_audio_available_ = false;
  std::string active_motion_name_;
  bool allow_active_motion_audio_to_finish_ = false;
  bool playback_completed_naturally_ = false;
  std::string pending_motion_name_;
  std::optional<std::chrono::steady_clock::time_point> delayed_stop_deadline_;
  std::string delayed_stop_motion_name_;

  std::unordered_map<std::string, CachedAudioClip> audio_cache_;
  std::unordered_set<std::string> missing_audio_logged_;
};

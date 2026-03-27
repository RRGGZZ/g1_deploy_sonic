#include "audio_thread.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>

#include "../../thirdparty/unitree_sdk2/example/g1/audio/wav.hpp"

namespace {

constexpr char kMotionAudioAppName[] = "gear_sonic_motion_audio";
constexpr int kPlaybackSampleRate = 16000;
constexpr std::size_t kChunkSizeBytes = 9600;  // 300 ms of 16 kHz mono PCM16
constexpr auto kLoopSleep = std::chrono::milliseconds(10);
const std::string kWarningStreamingDataAbsent = "Streaming data absent";
constexpr double kTargetSpeechRms = 14000.0;
constexpr double kMaxSpeechGain = 6.0;
constexpr double kOutputPeakTarget = 0.98;
constexpr auto kChunkLeadTime = std::chrono::milliseconds(40);
constexpr int kMotionAudioStartWindowFrames = 3;
// Allow longer grace so natural motion transitions don't cut audio tail.
constexpr auto kMotionAudioStopGrace = std::chrono::seconds(4);
// Unitree speaker playback still truncates the tail slightly if PlayStop is
// sent immediately after the final chunk drains, so keep an extra 1 s grace.
constexpr auto kPlaybackDrainPadding = std::chrono::milliseconds(2200);

std::string ZeroPadNumber(int value, int width) {
  std::ostringstream oss;
  oss << std::setw(width) << std::setfill('0') << value;
  return oss.str();
}

std::chrono::milliseconds ChunkPlaybackDuration(std::size_t chunk_size_bytes) {
  constexpr double bytes_per_second =
      static_cast<double>(kPlaybackSampleRate) * sizeof(int16_t);
  const auto duration_ms = static_cast<int64_t>(std::llround(
      1000.0 * static_cast<double>(chunk_size_bytes) / bytes_per_second));
  return std::chrono::milliseconds(std::max<int64_t>(1, duration_ms));
}

bool SleepInterruptibly(std::stop_token st, std::chrono::milliseconds duration) {
  auto remaining = duration;
  while (!st.stop_requested() && remaining.count() > 0) {
    const auto slice = std::min(remaining, std::chrono::milliseconds(10));
    std::this_thread::sleep_for(slice);
    remaining -= slice;
  }
  return !st.stop_requested();
}

std::pair<std::vector<int16_t>, double> BoostSpeechLoudness(
    const std::vector<int16_t>& samples) {
  if (samples.empty()) {
    return {samples, 1.0};
  }

  double energy = 0.0;
  for (const auto sample : samples) {
    const double value = static_cast<double>(sample);
    energy += value * value;
  }
  const double rms = std::sqrt(energy / static_cast<double>(samples.size()));
  if (rms < 1.0) {
    return {samples, 1.0};
  }

  const double gain = std::clamp(kTargetSpeechRms / rms, 1.0, kMaxSpeechGain);

  std::vector<double> processed(samples.size(), 0.0);
  double peak = 0.0;
  for (std::size_t i = 0; i < samples.size(); ++i) {
    const double normalized =
        gain * static_cast<double>(samples[i]) /
        static_cast<double>(std::numeric_limits<int16_t>::max());
    const double limited = std::tanh(normalized);
    processed[i] = limited;
    peak = std::max(peak, std::abs(limited));
  }

  if (peak < 1e-9) {
    return {samples, gain};
  }

  const double peak_gain = kOutputPeakTarget / peak;
  std::vector<int16_t> boosted(samples.size(), 0);
  for (std::size_t i = 0; i < samples.size(); ++i) {
    const double scaled = std::clamp(processed[i] * peak_gain, -1.0, 1.0);
    boosted[i] = static_cast<int16_t>(std::lround(
        scaled * static_cast<double>(std::numeric_limits<int16_t>::max())));
  }

  return {boosted, gain};
}

}  // namespace

AudioThread::AudioThread(const std::string& motion_audio_dir)
    : client_(), motion_audio_dir_(motion_audio_dir) {
  client_.Init();
  client_.SetTimeout(10.0f);
  client_.SetVolume(100);

  if (!motion_audio_dir_.empty()) {
    std::error_code ec;
    motion_audio_available_ =
        std::filesystem::exists(motion_audio_dir_, ec) &&
        std::filesystem::is_directory(motion_audio_dir_, ec);
    if (motion_audio_available_) {
      std::cout << "✓ Motion audio directory: " << motion_audio_dir_ << std::endl;
    } else {
      std::cout << "⚠ Motion audio directory not found, motion audio disabled: "
                << motion_audio_dir_ << std::endl;
    }
  }

  if (motion_audio_available_) {
    std::vector<std::filesystem::path> audio_files;
    std::error_code ec;
    for (const auto& entry :
         std::filesystem::directory_iterator(motion_audio_dir_, ec)) {
      if (ec) {
        break;
      }
      if (!entry.is_regular_file()) {
        continue;
      }
      if (entry.path().extension() == ".wav") {
        audio_files.push_back(entry.path());
      }
    }
    std::sort(audio_files.begin(), audio_files.end());

    int preloaded_count = 0;
    for (const auto& audio_file : audio_files) {
      auto clip = LoadAudioClip(audio_file);
      if (!clip.has_value()) {
        continue;
      }
      audio_cache_[audio_file.lexically_normal().string()] = *clip;
      ++preloaded_count;
    }
    std::cout << "Preloaded " << preloaded_count
              << " motion audio clip(s) into memory." << std::endl;
  }

  thread_ = std::jthread([this](std::stop_token st) { loop(st); });
}

AudioThread::~AudioThread() {
  if (thread_.joinable()) {
    thread_.request_stop();
    thread_.join();
  }
  StopMotionPlayback();
}

void AudioThread::SetCommand(const AudioCommand& command) {
  std::lock_guard<std::mutex> lock(command_mutex_);
  command_ = command;
}

void AudioThread::loop(std::stop_token st) {
  while (!st.stop_requested()) {
    AudioCommand command;
    {
      std::lock_guard<std::mutex> lock(command_mutex_);
      command = command_;
    }

    if (command.streaming_data_absent && !command_last_.streaming_data_absent) {
      std::lock_guard<std::mutex> client_lock(client_mutex_);
      client_.TtsMaker(kWarningStreamingDataAbsent, 1);
    }

    const auto now = std::chrono::steady_clock::now();
    std::string active_motion_name;
    bool allow_active_motion_audio_to_finish = false;
    std::string pending_motion_name;
    bool delayed_stop_armed = false;
    bool delayed_stop_elapsed = false;
    {
      std::lock_guard<std::mutex> playback_lock(playback_mutex_);
      active_motion_name = active_motion_name_;
      allow_active_motion_audio_to_finish = allow_active_motion_audio_to_finish_;
      pending_motion_name = pending_motion_name_;
      delayed_stop_armed =
          !active_motion_name_.empty() &&
          delayed_stop_deadline_.has_value() &&
          delayed_stop_motion_name_ == active_motion_name_;
      delayed_stop_elapsed =
          delayed_stop_armed && now >= *delayed_stop_deadline_;
    }

    const bool motion_finished_naturally =
        !active_motion_name.empty() &&
        command_last_.motion_playing &&
        !command.motion_playing &&
        command.current_frame == 0 &&
        command_last_.motion_name == active_motion_name;
    if (motion_finished_naturally) {
      std::lock_guard<std::mutex> playback_lock(playback_mutex_);
      if (active_motion_name_ == active_motion_name) {
        allow_active_motion_audio_to_finish_ = true;
        allow_active_motion_audio_to_finish = true;
        delayed_stop_deadline_.reset();
        delayed_stop_motion_name_.clear();
        delayed_stop_armed = false;
        delayed_stop_elapsed = false;
      }
    }

    const bool raw_should_stop_motion_audio =
        !active_motion_name.empty() &&
        !allow_active_motion_audio_to_finish &&
        (!command.motion_audio_enabled || command.motion_name.empty() ||
         command.motion_name != active_motion_name);

    if (raw_should_stop_motion_audio) {
      std::lock_guard<std::mutex> playback_lock(playback_mutex_);
      if (active_motion_name_ == active_motion_name) {
        if (!delayed_stop_deadline_.has_value() ||
            delayed_stop_motion_name_ != active_motion_name_) {
          delayed_stop_deadline_ = now + kMotionAudioStopGrace;
          delayed_stop_motion_name_ = active_motion_name_;
        }
        delayed_stop_armed =
            delayed_stop_deadline_.has_value() &&
            delayed_stop_motion_name_ == active_motion_name_;
        delayed_stop_elapsed =
            delayed_stop_armed && now >= *delayed_stop_deadline_;
      }
    } else if (!active_motion_name.empty()) {
      std::lock_guard<std::mutex> playback_lock(playback_mutex_);
      if (delayed_stop_motion_name_ == active_motion_name_) {
        delayed_stop_deadline_.reset();
        delayed_stop_motion_name_.clear();
      }
      delayed_stop_armed = false;
      delayed_stop_elapsed = false;
    }

    const bool should_stop_motion_audio =
        raw_should_stop_motion_audio && delayed_stop_elapsed;
    const bool stop_grace_pending =
        raw_should_stop_motion_audio && !delayed_stop_elapsed;

    const bool should_start_motion_audio =
        motion_audio_available_ && command.motion_audio_enabled &&
        command.motion_playing &&
        command.current_frame <= kMotionAudioStartWindowFrames &&
        !command.motion_name.empty() &&
        (!command_last_.motion_playing ||
         command_last_.motion_name != command.motion_name ||
         command_last_.current_frame > kMotionAudioStartWindowFrames);

    const bool should_queue_motion_audio =
        should_start_motion_audio &&
        !active_motion_name.empty() &&
        (allow_active_motion_audio_to_finish || stop_grace_pending) &&
        command.motion_name != active_motion_name;

    if (!pending_motion_name.empty() &&
        (!command.motion_audio_enabled || command.motion_name != pending_motion_name)) {
      std::lock_guard<std::mutex> playback_lock(playback_mutex_);
      if (pending_motion_name_ == pending_motion_name) {
        pending_motion_name_.clear();
        pending_motion_name.clear();
      }
    }

    const bool should_start_queued_motion_audio =
        motion_audio_available_ &&
        active_motion_name.empty() &&
        !pending_motion_name.empty() &&
        command.motion_audio_enabled &&
        command.motion_name == pending_motion_name;

    if (should_stop_motion_audio) {
      StopMotionPlayback();
    }
    if (should_queue_motion_audio) {
      std::lock_guard<std::mutex> playback_lock(playback_mutex_);
      pending_motion_name_ = command.motion_name;
    } else if (should_start_motion_audio) {
      StartMotionPlayback(command.motion_name);
    } else if (should_start_queued_motion_audio) {
      std::string queued_motion_name;
      {
        std::lock_guard<std::mutex> playback_lock(playback_mutex_);
        queued_motion_name = pending_motion_name_;
        pending_motion_name_.clear();
      }
      if (!queued_motion_name.empty()) {
        StartMotionPlayback(queued_motion_name);
      }
    }

    command_last_ = command;
    std::this_thread::sleep_for(kLoopSleep);
  }

  StopMotionPlayback();
}

void AudioThread::StartMotionPlayback(const std::string& motion_name) {
  if (!motion_audio_available_) {
    return;
  }

  StopMotionPlayback();

  auto audio_path = ResolveAudioPathForMotion(motion_name);
  if (!audio_path.has_value()) {
    return;
  }

  {
    std::lock_guard<std::mutex> playback_lock(playback_mutex_);
    active_motion_name_ = motion_name;
    allow_active_motion_audio_to_finish_ = false;
    playback_completed_naturally_ = false;
    delayed_stop_deadline_.reset();
    delayed_stop_motion_name_.clear();
  }

  std::cout << "Starting motion audio for " << motion_name << ": " << *audio_path
            << std::endl;
  playback_thread_ = std::jthread(
      [this, motion_name, audio_path = *audio_path](std::stop_token st) {
        PlaybackMotionAudio(st, motion_name, audio_path);
      });
}

void AudioThread::StopMotionPlayback() {
  bool should_force_playstop = false;
  if (playback_thread_.joinable()) {
    playback_thread_.request_stop();
    playback_thread_.join();

    {
      std::lock_guard<std::mutex> playback_lock(playback_mutex_);
      should_force_playstop = !playback_completed_naturally_;
      playback_completed_naturally_ = false;
    }
  }

  if (should_force_playstop) {
    std::lock_guard<std::mutex> client_lock(client_mutex_);
    client_.PlayStop(kMotionAudioAppName);
  }

  std::lock_guard<std::mutex> playback_lock(playback_mutex_);
  active_motion_name_.clear();
  allow_active_motion_audio_to_finish_ = false;
  playback_completed_naturally_ = false;
  pending_motion_name_.clear();
  delayed_stop_deadline_.reset();
  delayed_stop_motion_name_.clear();
}

void AudioThread::PlaybackMotionAudio(std::stop_token st,
                                      std::string motion_name,
                                      std::filesystem::path audio_path) {
  auto clip = GetOrLoadAudioClip(audio_path);
  if (!clip.has_value() || clip->pcm_bytes_16khz_mono.empty()) {
    std::lock_guard<std::mutex> playback_lock(playback_mutex_);
    if (active_motion_name_ == motion_name) {
      active_motion_name_.clear();
      allow_active_motion_audio_to_finish_ = false;
      playback_completed_naturally_ = false;
      pending_motion_name_.clear();
    }
    return;
  }

  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  const auto stream_id = std::to_string(
      std::chrono::duration_cast<std::chrono::milliseconds>(now).count());

  std::size_t offset = 0;
  std::chrono::milliseconds last_chunk_duration{0};
  while (!st.stop_requested() && offset < clip->pcm_bytes_16khz_mono.size()) {
    const std::size_t remaining = clip->pcm_bytes_16khz_mono.size() - offset;
    const std::size_t current_chunk_size =
        std::min<std::size_t>(kChunkSizeBytes, remaining);
    last_chunk_duration = ChunkPlaybackDuration(current_chunk_size);

    std::vector<uint8_t> chunk(
        clip->pcm_bytes_16khz_mono.begin() + static_cast<std::ptrdiff_t>(offset),
        clip->pcm_bytes_16khz_mono.begin() +
            static_cast<std::ptrdiff_t>(offset + current_chunk_size));
    {
      std::lock_guard<std::mutex> client_lock(client_mutex_);
      client_.SetVolume(100);
      client_.PlayStream(kMotionAudioAppName, stream_id, chunk);
    }

    offset += current_chunk_size;
    if (!st.stop_requested() && offset < clip->pcm_bytes_16khz_mono.size()) {
      const auto send_interval =
          last_chunk_duration > kChunkLeadTime
              ? last_chunk_duration - kChunkLeadTime
              : std::chrono::milliseconds(1);
      if (!SleepInterruptibly(st, send_interval)) {
        break;
      }
    }
  }

  if (!st.stop_requested() && last_chunk_duration.count() > 0) {
    SleepInterruptibly(st, last_chunk_duration + kPlaybackDrainPadding);
  }

  const bool completed_naturally =
      !st.stop_requested() &&
      offset >= clip->pcm_bytes_16khz_mono.size();

  std::lock_guard<std::mutex> playback_lock(playback_mutex_);
  if (active_motion_name_ == motion_name) {
    active_motion_name_.clear();
    allow_active_motion_audio_to_finish_ = false;
    playback_completed_naturally_ = completed_naturally;
    delayed_stop_deadline_.reset();
    delayed_stop_motion_name_.clear();
  }
}

std::optional<std::filesystem::path> AudioThread::ResolveAudioPathForMotion(
    const std::string& motion_name) {
  if (!motion_audio_available_) {
    return std::nullopt;
  }

  for (const auto& candidate : BuildAudioFileCandidates(motion_name)) {
    const auto audio_path = motion_audio_dir_ / candidate;
    std::error_code ec;
    if (std::filesystem::exists(audio_path, ec) &&
        std::filesystem::is_regular_file(audio_path, ec)) {
      return audio_path;
    }
  }

  if (missing_audio_logged_.insert(motion_name).second) {
    std::cout << "⚠ No matching audio file found for motion " << motion_name
              << " in " << motion_audio_dir_ << std::endl;
  }
  return std::nullopt;
}

std::optional<AudioThread::CachedAudioClip> AudioThread::GetOrLoadAudioClip(
    const std::filesystem::path& audio_path) {
  const auto cache_key = audio_path.lexically_normal().string();
  {
    std::lock_guard<std::mutex> cache_lock(cache_mutex_);
    auto it = audio_cache_.find(cache_key);
    if (it != audio_cache_.end()) {
      return it->second;
    }
  }

  auto clip = LoadAudioClip(audio_path);
  if (!clip.has_value()) {
    return std::nullopt;
  }

  {
    std::lock_guard<std::mutex> cache_lock(cache_mutex_);
    audio_cache_[cache_key] = *clip;
  }
  return clip;
}

std::optional<AudioThread::CachedAudioClip> AudioThread::LoadAudioClip(
    const std::filesystem::path& audio_path) const {
  int32_t sample_rate = -1;
  int8_t num_channels = 0;
  bool file_ok = false;
  std::vector<uint8_t> pcm_bytes =
      ReadWave(audio_path.string(), &sample_rate, &num_channels, &file_ok);

  if (!file_ok || pcm_bytes.empty()) {
    std::cout << "✗ Failed to read audio file: " << audio_path << std::endl;
    return std::nullopt;
  }
  if (sample_rate <= 0 || num_channels <= 0) {
    std::cout << "✗ Invalid audio format metadata in: " << audio_path << std::endl;
    return std::nullopt;
  }

  auto decoded_samples = DecodePcm16(pcm_bytes);
  auto mono_samples = DownmixToMono(decoded_samples, num_channels);
  auto resampled_samples =
      sample_rate == kTargetSampleRate
          ? mono_samples
          : ResampleLinear(mono_samples, sample_rate, kTargetSampleRate);
  auto [boosted_samples, applied_gain] = BoostSpeechLoudness(resampled_samples);

  if (boosted_samples.empty()) {
    std::cout << "✗ Audio file produced no output samples: " << audio_path
              << std::endl;
    return std::nullopt;
  }

  if (sample_rate != kTargetSampleRate || num_channels != 1 ||
      applied_gain > 1.05) {
    std::cout << "Prepared motion audio: " << audio_path
              << " (source " << sample_rate << " Hz, "
              << static_cast<int>(num_channels) << " ch"
              << ", gain " << std::setprecision(2) << applied_gain << "x)"
              << std::defaultfloat << std::endl;
  }

  CachedAudioClip clip;
  clip.pcm_bytes_16khz_mono = EncodePcm16(boosted_samples);
  clip.sample_rate = kTargetSampleRate;
  clip.num_channels = 1;
  return clip;
}

std::vector<std::string> AudioThread::BuildAudioFileCandidates(
    const std::string& motion_name) {
  std::vector<std::string> candidates;
  std::unordered_set<std::string> seen;

  auto push_candidate = [&](const std::string& stem) {
    if (stem.empty()) {
      return;
    }
    const std::string filename = stem + ".wav";
    if (seen.insert(filename).second) {
      candidates.push_back(filename);
    }
  };

  push_candidate(motion_name);

  std::size_t suffix_start = motion_name.size();
  while (suffix_start > 0 &&
         std::isdigit(static_cast<unsigned char>(motion_name[suffix_start - 1]))) {
    --suffix_start;
  }

  if (suffix_start < motion_name.size()) {
    const auto numeric_suffix = motion_name.substr(suffix_start);
    push_candidate(numeric_suffix);

    try {
      const int numeric_value = std::stoi(numeric_suffix);
      push_candidate(std::to_string(numeric_value));
      push_candidate(ZeroPadNumber(numeric_value, 2));
      push_candidate(ZeroPadNumber(numeric_value, 3));
    } catch (const std::exception&) {
      // Keep the direct suffix candidate only.
    }
  }

  return candidates;
}

std::vector<int16_t> AudioThread::DecodePcm16(
    const std::vector<uint8_t>& pcm_bytes) {
  std::vector<int16_t> samples;
  samples.reserve(pcm_bytes.size() / 2);

  for (std::size_t i = 0; i + 1 < pcm_bytes.size(); i += 2) {
    const auto low = static_cast<uint16_t>(pcm_bytes[i]);
    const auto high = static_cast<uint16_t>(pcm_bytes[i + 1]) << 8;
    samples.push_back(static_cast<int16_t>(low | high));
  }

  return samples;
}

std::vector<int16_t> AudioThread::DownmixToMono(
    const std::vector<int16_t>& samples, int num_channels) {
  if (num_channels <= 1) {
    return samples;
  }

  const std::size_t frame_count = samples.size() / static_cast<std::size_t>(num_channels);
  std::vector<int16_t> mono_samples(frame_count, 0);

  for (std::size_t frame = 0; frame < frame_count; ++frame) {
    int32_t mixed = 0;
    for (int channel = 0; channel < num_channels; ++channel) {
      mixed += samples[frame * static_cast<std::size_t>(num_channels) + channel];
    }
    mono_samples[frame] = static_cast<int16_t>(mixed / num_channels);
  }

  return mono_samples;
}

std::vector<int16_t> AudioThread::ResampleLinear(
    const std::vector<int16_t>& samples,
    int source_sample_rate,
    int target_sample_rate) {
  if (samples.empty() || source_sample_rate <= 0 || target_sample_rate <= 0) {
    return {};
  }
  if (source_sample_rate == target_sample_rate || samples.size() == 1) {
    return samples;
  }

    // Preserve full clip duration by sizing from sample-count ratio, and enforce
    // endpoint alignment so the last source sample is always represented.
    const auto output_size = static_cast<std::size_t>(std::max<int64_t>(
      2,
      static_cast<int64_t>(std::llround(
        static_cast<double>(samples.size()) * target_sample_rate /
        source_sample_rate))));

  std::vector<int16_t> resampled(output_size, 0);
  for (std::size_t out_idx = 0; out_idx < output_size; ++out_idx) {
    const double source_position =
      static_cast<double>(out_idx) * static_cast<double>(samples.size() - 1) /
      static_cast<double>(output_size - 1);
    const auto left_index = static_cast<std::size_t>(std::floor(source_position));
    const auto right_index =
        std::min(left_index + 1, samples.size() - 1);
    const double alpha = source_position - left_index;
    const double blended =
        (1.0 - alpha) * static_cast<double>(samples[left_index]) +
        alpha * static_cast<double>(samples[right_index]);
    const double clamped = std::clamp(
        blended,
        static_cast<double>(std::numeric_limits<int16_t>::min()),
        static_cast<double>(std::numeric_limits<int16_t>::max()));
    resampled[out_idx] = static_cast<int16_t>(std::lround(clamped));
  }

  return resampled;
}

std::vector<uint8_t> AudioThread::EncodePcm16(
    const std::vector<int16_t>& samples) {
  std::vector<uint8_t> pcm_bytes(samples.size() * 2);

  for (std::size_t i = 0; i < samples.size(); ++i) {
    const auto value = static_cast<uint16_t>(samples[i]);
    pcm_bytes[i * 2] = static_cast<uint8_t>(value & 0xFF);
    pcm_bytes[i * 2 + 1] = static_cast<uint8_t>((value >> 8) & 0xFF);
  }

  return pcm_bytes;
}

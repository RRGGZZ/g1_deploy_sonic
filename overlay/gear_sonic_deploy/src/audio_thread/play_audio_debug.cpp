#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/g1/audio/g1_audio_client.hpp>

#include "../../thirdparty/unitree_sdk2/example/g1/audio/wav.hpp"

namespace {

constexpr char kAudioAppName[] = "gear_sonic_audio_debug";
constexpr std::size_t kChunkSizeBytes = 9600;  // 300 ms of 16 kHz mono PCM16
constexpr int kTargetSampleRate = 16000;
constexpr double kTargetSpeechRms = 14000.0;
constexpr double kMaxSpeechGain = 6.0;
constexpr double kOutputPeakTarget = 0.98;
constexpr auto kChunkLeadTime = std::chrono::milliseconds(40);
constexpr auto kPlaybackDrainPadding = std::chrono::milliseconds(120);

std::vector<int16_t> DecodePcm16(const std::vector<uint8_t>& pcm_bytes) {
  std::vector<int16_t> samples;
  samples.reserve(pcm_bytes.size() / 2);
  for (std::size_t i = 0; i + 1 < pcm_bytes.size(); i += 2) {
    const auto low = static_cast<uint16_t>(pcm_bytes[i]);
    const auto high = static_cast<uint16_t>(pcm_bytes[i + 1]) << 8;
    samples.push_back(static_cast<int16_t>(low | high));
  }
  return samples;
}

std::vector<int16_t> DownmixToMono(const std::vector<int16_t>& samples,
                                   int num_channels) {
  if (num_channels <= 1) {
    return samples;
  }

  const std::size_t frame_count =
      samples.size() / static_cast<std::size_t>(num_channels);
  std::vector<int16_t> mono_samples(frame_count, 0);

  for (std::size_t frame = 0; frame < frame_count; ++frame) {
    int32_t mixed = 0;
    for (int channel = 0; channel < num_channels; ++channel) {
      mixed +=
          samples[frame * static_cast<std::size_t>(num_channels) + channel];
    }
    mono_samples[frame] = static_cast<int16_t>(mixed / num_channels);
  }

  return mono_samples;
}

std::vector<int16_t> ResampleLinear(const std::vector<int16_t>& samples,
                                    int source_sample_rate,
                                    int target_sample_rate) {
  if (samples.empty() || source_sample_rate <= 0 || target_sample_rate <= 0) {
    return {};
  }
  if (source_sample_rate == target_sample_rate || samples.size() == 1) {
    return samples;
  }

  const double duration_seconds =
      static_cast<double>(samples.size() - 1) / source_sample_rate;
  const auto output_size = static_cast<std::size_t>(
                               std::floor(duration_seconds * target_sample_rate +
                                          1e-9)) +
                           1;

  std::vector<int16_t> resampled(output_size, 0);
  for (std::size_t out_idx = 0; out_idx < output_size; ++out_idx) {
    const double source_position =
        static_cast<double>(out_idx) * source_sample_rate / target_sample_rate;
    const auto left_index =
        static_cast<std::size_t>(std::floor(source_position));
    const auto right_index = std::min(left_index + 1, samples.size() - 1);
    const double alpha = source_position - left_index;
    const double blended =
        (1.0 - alpha) * static_cast<double>(samples[left_index]) +
        alpha * static_cast<double>(samples[right_index]);
    const double clamped = std::clamp(
        blended, static_cast<double>(std::numeric_limits<int16_t>::min()),
        static_cast<double>(std::numeric_limits<int16_t>::max()));
    resampled[out_idx] = static_cast<int16_t>(std::lround(clamped));
  }

  return resampled;
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

std::vector<uint8_t> EncodePcm16(const std::vector<int16_t>& samples) {
  std::vector<uint8_t> pcm_bytes(samples.size() * 2);
  for (std::size_t i = 0; i < samples.size(); ++i) {
    const auto value = static_cast<uint16_t>(samples[i]);
    pcm_bytes[i * 2] = static_cast<uint8_t>(value & 0xFF);
    pcm_bytes[i * 2 + 1] = static_cast<uint8_t>((value >> 8) & 0xFF);
  }
  return pcm_bytes;
}

std::chrono::milliseconds ChunkPlaybackDuration(std::size_t chunk_size_bytes) {
  constexpr double bytes_per_second =
      static_cast<double>(kTargetSampleRate) * sizeof(int16_t);
  const auto duration_ms = static_cast<int64_t>(std::llround(
      1000.0 * static_cast<double>(chunk_size_bytes) / bytes_per_second));
  return std::chrono::milliseconds(std::max<int64_t>(1, duration_ms));
}

int PlayAudioFile(const std::string& network_interface,
                  const std::string& wav_path) {
  unitree::robot::ChannelFactory::Instance()->Init(0, network_interface);

  unitree::robot::g1::AudioClient client;
  client.Init();
  client.SetTimeout(10.0f);
  client.SetVolume(100);

  int32_t sample_rate = -1;
  int8_t num_channels = 0;
  bool file_ok = false;
  std::vector<uint8_t> pcm_bytes =
      ReadWave(wav_path, &sample_rate, &num_channels, &file_ok);

  if (!file_ok || pcm_bytes.empty()) {
    std::cerr << "Failed to read WAV: " << wav_path << std::endl;
    return 1;
  }

  auto decoded_samples = DecodePcm16(pcm_bytes);
  auto mono_samples = DownmixToMono(decoded_samples, num_channels);
  auto resampled_samples =
      sample_rate == kTargetSampleRate
          ? mono_samples
          : ResampleLinear(mono_samples, sample_rate, kTargetSampleRate);
  auto [boosted_samples, applied_gain] = BoostSpeechLoudness(resampled_samples);
  auto prepared_pcm = EncodePcm16(boosted_samples);

  std::cout << "Playing " << wav_path << std::endl;
  std::cout << "Source: " << sample_rate << " Hz, "
            << static_cast<int>(num_channels) << " channel(s)" << std::endl;
  std::cout << "Prepared: " << kTargetSampleRate << " Hz mono, gain "
            << std::fixed << std::setprecision(2) << applied_gain << "x"
            << std::defaultfloat << std::endl;

  const auto now = std::chrono::steady_clock::now().time_since_epoch();
  const auto stream_id = std::to_string(
      std::chrono::duration_cast<std::chrono::milliseconds>(now).count());

  std::size_t offset = 0;
  std::chrono::milliseconds last_chunk_duration{0};
  while (offset < prepared_pcm.size()) {
    const std::size_t remaining = prepared_pcm.size() - offset;
    const std::size_t current_chunk_size =
        std::min<std::size_t>(kChunkSizeBytes, remaining);
    last_chunk_duration = ChunkPlaybackDuration(current_chunk_size);
    std::vector<uint8_t> chunk(
        prepared_pcm.begin() + static_cast<std::ptrdiff_t>(offset),
        prepared_pcm.begin() +
            static_cast<std::ptrdiff_t>(offset + current_chunk_size));
    client.SetVolume(100);
    client.PlayStream(kAudioAppName, stream_id, chunk);
    offset += current_chunk_size;
    if (offset < prepared_pcm.size()) {
      const auto send_interval =
          last_chunk_duration > kChunkLeadTime
              ? last_chunk_duration - kChunkLeadTime
              : std::chrono::milliseconds(1);
      std::this_thread::sleep_for(send_interval);
    }
  }

  if (last_chunk_duration.count() > 0) {
    std::this_thread::sleep_for(last_chunk_duration + kPlaybackDrainPadding);
  }
  client.PlayStop(kAudioAppName);
  std::cout << "Playback finished." << std::endl;
  return 0;
}

}  // namespace

int main(int argc, char const* argv[]) {
  if (argc < 3) {
    std::cout << "Usage: " << argv[0]
              << " <network_interface> <wav_path>\n"
              << "Example: " << argv[0]
              << " enp5s0 reference/example_scalelab/qwen_audio/audio_en/01.wav"
              << std::endl;
    return 1;
  }

  return PlayAudioFile(argv[1], argv[2]);
}

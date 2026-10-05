"""Differential-check control-register batching against the actual SDK methods.

Compiles extracted original and patched D3D12 methods, real base register side
effects, real RegisterFile metadata, and the installed runtime's GPU logger.
No graphics device is created. Run in a Visual Studio developer environment.
When constant reuse is detected, a retained clean binding must still contain
exactly its pre-call referenced bytes; all other variants require exact dirtiness.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True, type=Path)
parser.add_argument('--sdk', required=True, type=Path)
parser.add_argument('--cxx', required=True)
parser.add_argument('--output', type=Path)
args = parser.parse_args()
source = args.source.resolve(strict=True)
sdk = args.sdk.resolve(strict=True)
compiler = shutil.which(args.cxx) or str(Path(args.cxx).resolve(strict=True))
temporary = tempfile.TemporaryDirectory(prefix='renderer-control-') if args.output is None else None
output = Path(temporary.name) if temporary else args.output.resolve()
output.mkdir(parents=True, exist_ok=True)


def extract(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    end, depth = brace + 1, 1
    while depth:
        if text[end] == '{': depth += 1
        elif text[end] == '}': depth -= 1
        end += 1
    return text[start:end] + '\n'


base = (source / 'src/graphics/command_processor.cpp').read_text()
backend_path = 'src/graphics/d3d12/command_processor.cpp'
backend = (source / backend_path).read_text()
original = subprocess.check_output(['git', 'show', 'HEAD:' + backend_path], cwd=source, text=True)
original_method = extract(original, 'void D3D12CommandProcessor::WriteRegistersFromMem(')
original_method = original_method.replace('D3D12CommandProcessor::', 'OriginalD3D12::', 1)
patched_method = extract(backend, 'void D3D12CommandProcessor::WriteRegistersFromMem(')
if 'num_registers <= 0x4000 - start_index' not in patched_method:
    parser.error('--source must contain the bounded control-register batching patch')
methods = '\n'.join(extract(base, sig) for sig in (
    'void CommandProcessor::WriteRegister(', 'void CommandProcessor::WriteRegistersFromMem(',
    'void CommandProcessor::WriteRegisterRangeFromRing('))
methods += '\n'.join(extract(backend, sig) for sig in (
    'void D3D12CommandProcessor::WriteRegister(',
    'void D3D12CommandProcessor::InvalidateVertexBufferResidency(',
    'void D3D12CommandProcessor::InvalidateVertexBufferResidencyRange('))

prefix = r'''

#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <immintrin.h>
#include <random>
#include <unordered_map>
#include <vector>
#include <rex/graphics/register_file.h>
#include <rex/memory/ring_buffer.h>
#include <rex/logging.h>
#include <spdlog/sinks/sink.h>
#include <spdlog/formatter.h>
#ifdef NDEBUG
#error Register regression assertions must remain enabled.
#endif
namespace rex::graphics {
struct MockMemory {
  std::array<uint8_t, 4096> storage{};
  uint8_t* TranslatePhysical(uint32_t address) {
    assert(address + 4 <= storage.size()); return storage.data() + address;
  }
};
class CommandProcessor {
 public:
  RegisterFile regs;
  RegisterFile* register_file_ = &regs;
  MockMemory mem;
  MockMemory* memory_ = &mem;
  std::unordered_map<uint32_t, uint32_t> extended_register_values_;
  reg::DC_LUT_30_COLOR gamma_ramp_256_entry_table_[256]{};
  reg::DC_LUT_PWL_DATA gamma_ramp_pwl_rgb_[128][3]{};
  uint32_t gamma_ramp_rw_component_ = 0;
  uint32_t gamma256_notifications = 0, gamma_pwl_notifications = 0;
  virtual ~CommandProcessor() = default;
  virtual void WriteRegister(uint32_t index, uint32_t value);
  virtual void WriteRegistersFromMem(uint32_t start, uint32_t* base, uint32_t count);
  virtual void WriteRegisterRangeFromRing(memory::RingBuffer*, uint32_t, uint32_t);
  void OnGammaRamp256EntryTableValueWritten() { ++gamma256_notifications; }
  void OnGammaRampPWLValueWritten() { ++gamma_pwl_notifications; }
  bool ExecutePacketType0(memory::RingBuffer*, uint32_t);
  bool OriginalType0(memory::RingBuffer*, uint32_t);
};
struct TextureNotifications {
  uint64_t dirty = 0;
  void TextureFetchConstantWritten(uint32_t i) { dirty |= 1ull << i; }
  void TextureFetchConstantsWritten(uint32_t first, uint32_t last) {
    for (uint32_t i = first; i <= last; ++i) TextureFetchConstantWritten(i);
  }
};
class D3D12CommandProcessor : public CommandProcessor {
 public:
  bool frame_open_ = true;
  uint64_t current_float_constant_map_vertex_[4]{};
  uint64_t current_float_constant_map_pixel_[4]{};
  struct Binding { bool up_to_date = true; };
  Binding cbuffer_binding_float_pixel_, cbuffer_binding_float_vertex_;
  Binding cbuffer_binding_bool_loop_, cbuffer_binding_fetch_;
  TextureNotifications texture;
  TextureNotifications* texture_cache_ = &texture;
  std::array<uint32_t, 96> vertex_buffer_states_{};
  uint64_t vertex_buffers_in_sync_[2] = {UINT64_MAX, UINT64_MAX};
  void WriteRegister(uint32_t, uint32_t) override;
  void WriteRegistersFromMem(uint32_t, uint32_t*, uint32_t) override;
  void InvalidateVertexBufferResidency(uint32_t);
  void InvalidateVertexBufferResidencyRange(uint32_t, uint32_t);
};
class OriginalD3D12 : public D3D12CommandProcessor { public: void WriteRegistersFromMem(uint32_t, uint32_t*, uint32_t) override; };

'''

suffix = r'''
}
using namespace rex::graphics;
class CaptureSink : public spdlog::sinks::sink {
 public:
  std::vector<std::pair<int, std::string>> messages;
  void log(const spdlog::details::log_msg& msg) override {
    messages.emplace_back(int(msg.level), std::string(msg.payload.data(), msg.payload.size()));
  }
  void flush() override {}
  void set_pattern(const std::string&) override {}
  void set_formatter(std::unique_ptr<spdlog::formatter>) override {}
};
static std::shared_ptr<CaptureSink> capture;
static unsigned cases = 0;
static void Compare(const D3D12CommandProcessor& a, const D3D12CommandProcessor& b,
                    const RegisterFile* before = nullptr) {
  assert(!memcmp(a.regs.values, b.regs.values, sizeof(a.regs.values)));
  assert(a.extended_register_values_ == b.extended_register_values_);
  assert(a.mem.storage == b.mem.storage);
  assert(!memcmp(a.gamma_ramp_256_entry_table_, b.gamma_ramp_256_entry_table_, sizeof(a.gamma_ramp_256_entry_table_)));
  assert(!memcmp(a.gamma_ramp_pwl_rgb_, b.gamma_ramp_pwl_rgb_, sizeof(a.gamma_ramp_pwl_rgb_)));
  assert(a.gamma_ramp_rw_component_ == b.gamma_ramp_rw_component_);
  assert(a.gamma256_notifications == b.gamma256_notifications);
  assert(a.gamma_pwl_notifications == b.gamma_pwl_notifications);
#ifdef TEST_CONSTANT_REUSE
  // A reuse variant may retain only a clean buffer whose referenced bytes
  // still equal its pre-call contents. Every other difference is a failure.
  for (unsigned bank = 0; bank < 3; ++bank) {
    bool clean_a = bank == 0 ? a.cbuffer_binding_float_vertex_.up_to_date :
        bank == 1 ? a.cbuffer_binding_float_pixel_.up_to_date : a.cbuffer_binding_bool_loop_.up_to_date;
    bool clean_b = bank == 0 ? b.cbuffer_binding_float_vertex_.up_to_date :
        bank == 1 ? b.cbuffer_binding_float_pixel_.up_to_date : b.cbuffer_binding_bool_loop_.up_to_date;
    if (clean_a != clean_b) {
      assert(clean_a && !clean_b && before);
      if (bank < 2) {
        const uint64_t* usage = bank ? a.current_float_constant_map_pixel_ : a.current_float_constant_map_vertex_;
        uint32_t base = XE_GPU_REG_SHADER_CONSTANT_000_X + bank * 1024;
        for (unsigned i = 0; i < 256; ++i) if ((usage[i / 64] >> (i % 64)) & 1)
          assert(!memcmp(a.regs.values + base + i * 4, before->values + base + i * 4, 16));
      } else {
        assert(!memcmp(a.regs.values + XE_GPU_REG_SHADER_CONSTANT_BOOL_000_031,
                       before->values + XE_GPU_REG_SHADER_CONSTANT_BOOL_000_031, 40 * 4));
      }
    }
  }
#else
  assert(a.cbuffer_binding_float_pixel_.up_to_date == b.cbuffer_binding_float_pixel_.up_to_date);
  assert(a.cbuffer_binding_float_vertex_.up_to_date == b.cbuffer_binding_float_vertex_.up_to_date);
  assert(a.cbuffer_binding_bool_loop_.up_to_date == b.cbuffer_binding_bool_loop_.up_to_date);
#endif
  assert(a.cbuffer_binding_fetch_.up_to_date == b.cbuffer_binding_fetch_.up_to_date);
  assert(a.vertex_buffers_in_sync_[0] == b.vertex_buffers_in_sync_[0]);
  assert(a.vertex_buffers_in_sync_[1] == b.vertex_buffers_in_sync_[1]);
  assert(a.texture.dirty == b.texture.dirty);
}
static void Check(uint32_t start, uint32_t count, bool ring_wrap = false, bool repeated = false,
                  int alias_offset = 100000, bool identical = false) {
  D3D12CommandProcessor actual;
  OriginalD3D12 expected;
  std::mt19937 random(start + count * 97);
  for (auto* p : {static_cast<D3D12CommandProcessor*>(&actual), static_cast<D3D12CommandProcessor*>(&expected)}) {
    p->regs[XE_GPU_REG_SCRATCH_ADDR] = 512;
    p->regs[XE_GPU_REG_SCRATCH_UMSK] = 0xff;
    p->regs[XE_GPU_REG_DC_LUT_WRITE_EN_MASK] = 7;
    p->regs[XE_GPU_REG_DC_LUT_RW_MODE] = start == XE_GPU_REG_DC_LUT_PWL_DATA;
    for (unsigned i = 0; i < 4; ++i) {
      p->current_float_constant_map_vertex_[i] = UINT64_MAX;
      p->current_float_constant_map_pixel_[i] = UINT64_MAX;
    }
  }
  std::vector<uint32_t> input(count + 8);
  for (unsigned i = 0; i < input.size(); ++i)
    input[i] = rex::byte_swap(identical && i < count ? actual.regs.values[start + i] : random());
  RegisterFile before = actual.regs;
  auto invoke = [&](auto& processor) {
    if (repeated) {
      for (unsigned i = 0; i < count; ++i) processor.WriteRegister(start, rex::byte_swap(input[i]));
    } else if (alias_offset != 100000) {
      for (unsigned i = 0; i < count + 2; ++i) processor.regs.values[start - 1 + i] = input[i];
      processor.WriteRegistersFromMem(start, processor.regs.values + start + alias_offset, count);
    } else if (ring_wrap) {
      rex::memory::RingBuffer ring(reinterpret_cast<uint8_t*>(input.data()), input.size() * 4);
      ring.set_read_offset((input.size() - 1) * 4);
      ring.set_write_offset((count - 1) * 4);
      processor.WriteRegisterRangeFromRing(&ring, start, count);
      assert(ring.read_count() == 0);
      assert(ring.read_offset() == (count - 1) * 4);
    } else {
      processor.WriteRegistersFromMem(start, input.data(), count);
    }
  };
  capture->messages.clear();
  invoke(expected);
  auto expected_messages = capture->messages;
  capture->messages.clear();
  invoke(actual);
  assert(capture->messages == expected_messages);
  Compare(actual, expected, &before);
  ++cases;
}
static void Benchmark() {
  rex::SetCategoryLevel(rex::log::gpu(), spdlog::level::info);
  const unsigned iterations = 10000;
  for (uint32_t count : {8u, 64u, 512u, 8192u}) {
    std::vector<uint32_t> input(count);
    for (unsigned i = 0; i < count; ++i) input[i] = rex::byte_swap(0x70000001u + i);
    for (bool optimized : {false, true}) {
      D3D12CommandProcessor patched;
      OriginalD3D12 original;
      D3D12CommandProcessor* processor = optimized ? &patched : &original;
      volatile uint32_t checksum = 0;
      const auto before = std::chrono::steady_clock::now();
      for (unsigned i = 0; i < iterations; ++i) {
        processor->WriteRegistersFromMem(0x2000, input.data(), count);
        checksum = checksum + processor->regs.values[0x2000 + i % count];
      }
      double elapsed = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - before).count();
      printf("MICROBENCH %s %u control dwords: %.3f us/batch, checksum %u (informational CPU fixture).\n",
          optimized ? "patched" : "original", count, elapsed / iterations, uint32_t(checksum));
    }
  }
}
int main() {
  capture = std::make_shared<CaptureSink>();
  rex::LogConfig config;
  config.extra_sinks.push_back(capture);
  rex::InitLogging(config);
  // Every control-register address, including known registers and metadata holes.
  for (auto level : {spdlog::level::info, spdlog::level::debug, spdlog::level::trace, spdlog::level::off}) {
    rex::SetCategoryLevel(rex::log::gpu(), level);
    for (uint32_t index = 0x2000; index < 0x4000; ++index) Check(index, 1);
    for (uint32_t start : {0x1fffu, 0x2000u, 0x2100u, 0x2200u, 0x2400u, 0x3ffdu, 0x3fffu, 0x4000u,
                          0x47feu, 0x4800u, 0x4900u, 0x4fffu, 0x5003u}) {
      for (uint32_t count : {0u, 1u, 2u, 4u, 12u, 64u, 256u}) {
        Check(start, count);
        if (count) Check(start, count, true);
      }
    }
    Check(0x2000, 8192);  // Full eligible block.
    Check(0x1fff, 8194);  // Both boundaries crossed: scalar fallback.
    for (int alias : {-1, 0, 1}) Check(0x2200, 12, false, false, alias);
    for (uint32_t special : {uint32_t(XE_GPU_REG_SCRATCH_REG0), uint32_t(XE_GPU_REG_COHER_STATUS_HOST),
         uint32_t(XE_GPU_REG_DC_LUT_RW_INDEX), uint32_t(XE_GPU_REG_DC_LUT_SEQ_COLOR),
         uint32_t(XE_GPU_REG_DC_LUT_PWL_DATA), uint32_t(XE_GPU_REG_DC_LUT_30_COLOR)}) {
      Check(special, 1);
      Check(special, 12, false, true);
    }
  }
  // Logger changes are consulted per batch; debug logging is never cached off.
  for (auto level : {spdlog::level::info, spdlog::level::debug, spdlog::level::info, spdlog::level::trace}) {
    rex::SetCategoryLevel(rex::log::gpu(), level);
    Check(0x3000, 12);
  }
#ifdef TEST_CONSTANT_REUSE
  for (uint32_t start : {0x4000u, 0x4001u, 0x43ffu, 0x4400u, 0x4900u, 0x4907u})
    for (uint32_t count : {1u, 4u, 8u, 32u}) for (bool wrap : {false, true})
      Check(start, count, wrap, false, 100000, true);
#endif
  printf("PASS: %u actual-method comparisons; ordered control bytes, all side effects, ring wrap, aliases, constant/fetch state and real GPU logger messages match.\n", cases);
  Benchmark();
  rex::ShutdownLogging();
}
'''

fixture = output / 'renderer_control_test.cpp'
reuse = '#define TEST_CONSTANT_REUSE\n' if 'copy_range_and_check_changes' in patched_method else ''
fixture.write_text(reuse + prefix + methods + patched_method + original_method + suffix)
executable = output / 'renderer_control_test.exe'
sources = [fixture, source / 'src/core/ring_buffer.cpp', source / 'src/graphics/register_file.cpp']
includes = [sdk / 'include', source / 'include']
libraries = [sdk / 'lib' / name for name in ('rexruntime.lib', 'spdlog.lib', 'fmt.lib')]
cl_style = Path(compiler).name.lower() in {'cl', 'cl.exe', 'clang-cl', 'clang-cl.exe'}
if cl_style:
    command = [compiler, '/nologo', '/MD', '/EHsc', '/O2', '/std:c++latest',
               '/DSPDLOG_COMPILED_LIB', '/DSPDLOG_FMT_EXTERNAL']
    if 'clang' in Path(compiler).name.lower(): command += ['/clang:-march=x86-64-v2']
    command += ['/I' + str(path) for path in includes]
    command += [str(path) for path in sources]
    command += ['/Fo' + str(output) + os.sep, '/Fe' + str(executable), '/link'] + [str(path) for path in libraries]
else:
    command = [compiler, '-std=c++23', '-O2', '-march=x86-64-v2', '-fms-runtime-lib=dll',
               '-Xlinker', '/NODEFAULTLIB:libcmt', '-DSPDLOG_COMPILED_LIB', '-DSPDLOG_FMT_EXTERNAL']
    command += ['-I' + str(path) for path in includes]
    command += [str(path) for path in sources]
    command += [str(path) for path in libraries] + ['-o', str(executable)]
subprocess.run(command, cwd=output, check=True)
environment = os.environ.copy()
environment['PATH'] = str(sdk / 'bin') + os.pathsep + environment.get('PATH', '')
subprocess.run([str(executable)], cwd=output, env=environment, check=True)
if args.output: print('Native register test artifacts:', output, flush=True)

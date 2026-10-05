"""Compare patched SDK TYPE0 against git HEAD using actual source method bodies.

A native boundary harness replaces GPU objects with state fixtures, but compiles
real base/D3D12/Vulkan command methods and RingBuffer. No GPU is created.
Run from a Visual Studio developer environment. Timing is informational only.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True, type=Path, help='Patched ReXGlue source git worktree')
parser.add_argument('--sdk', required=True, type=Path, help='Installed matching Windows SDK root')
parser.add_argument('--cxx', required=True, help='clang++, clang-cl or cl compiler')
parser.add_argument('--output', type=Path, help='Keep generated native fixture and executable here')
args = parser.parse_args()
sdk = args.source.resolve(strict=True)
installed = args.sdk.resolve(strict=True)
compiler = shutil.which(args.cxx) or str(Path(args.cxx).resolve(strict=True))
temporary = tempfile.TemporaryDirectory(prefix='renderer-type0-') if args.output is None else None
output = Path(temporary.name) if temporary else args.output.resolve()
output.mkdir(parents=True, exist_ok=True)

def function(path, signature):
    text = (sdk / path).read_text()
    start = text.index(signature)
    pos = text.index('{', start)
    depth = 1
    end = pos + 1
    while depth:
        if text[end] == '{': depth += 1
        elif text[end] == '}': depth -= 1
        end += 1
    return text[start:end] + '\n'

base_path = 'src/graphics/command_processor.cpp'
base_methods = '\n'.join(function(base_path, sig) for sig in [
    'void CommandProcessor::WriteRegister(',
    'void CommandProcessor::WriteRegistersFromMem(',
    'void CommandProcessor::WriteRegisterRangeFromRing(',
    'bool CommandProcessor::ExecutePacketType0(',
])
old_text = subprocess.check_output(['git', 'show', 'HEAD:' + base_path], cwd=sdk, text=True)
start = old_text.index('bool CommandProcessor::ExecutePacketType0(')
end = old_text.index('\nbool CommandProcessor::ExecutePacketType1(', start)
old_method = old_text[start:end].replace('ExecutePacketType0(', 'OriginalType0(', 1)
if 'WriteRegisterRangeFromRing(reader, base_index, count)' not in base_methods:
    parser.error('--source must contain the TYPE0 register batching patch')
if 'WriteRegisterRangeFromRing(' in old_method:
    parser.error('git HEAD must contain the original scalar TYPE0 baseline')
d3d_methods = '\n'.join(function('src/graphics/d3d12/command_processor.cpp', sig) for sig in [
    'void D3D12CommandProcessor::WriteRegister(',
    'void D3D12CommandProcessor::WriteRegistersFromMem(',
    'void D3D12CommandProcessor::InvalidateVertexBufferResidency(',
    'void D3D12CommandProcessor::InvalidateVertexBufferResidencyRange(',
])
vulkan_methods = '\n'.join(function('src/graphics/vulkan/command_processor.cpp', sig) for sig in [
    'void VulkanCommandProcessor::WriteRegister(',
    'void VulkanCommandProcessor::WriteRegistersFromMem(',
    'void VulkanCommandProcessor::InvalidateVertexBufferResidency(',
    'void VulkanCommandProcessor::InvalidateVertexBufferResidencyRange(',
])
# Use the real enum declaration without importing the GPU-dependent translator.
constant_buffer_enum = function('include/rex/graphics/pipeline/shader/spirv_translator.h',
                               'enum ConstantBuffer : uint32_t')

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
// Real logger access is used by the contained-control-block optimization; this
// fixture still omits diagnostics, which the dedicated control test compares.
#undef REXGPU_ERROR
#undef REXGPU_WARN
#undef REXGPU_DEBUG
#define REXGPU_ERROR(...) ((void)0)
#define REXGPU_WARN(...) ((void)0)
#define REXGPU_DEBUG(...) ((void)0)
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
'''
prefix += 'struct SpirvShaderTranslator {' + constant_buffer_enum + ';};\n'
prefix += r'''
// Reuse only state-fixture storage; all Vulkan command methods below come from
// the SDK verbatim and explicitly call CommandProcessor for scalar fallback.
class VulkanCommandProcessor : public D3D12CommandProcessor {
 public:
  uint32_t current_constant_buffers_up_to_date_ = UINT32_MAX;
  void WriteRegister(uint32_t, uint32_t) override;
  void WriteRegistersFromMem(uint32_t, uint32_t*, uint32_t) override;
  void InvalidateVertexBufferResidency(uint32_t);
  void InvalidateVertexBufferResidencyRange(uint32_t, uint32_t);
};
'''

suffix = r'''
}
using namespace rex::graphics;
#ifdef TEST_VULKAN
using Backend = VulkanCommandProcessor;
#else
using Backend = D3D12CommandProcessor;
#endif
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
#if defined(TEST_CONSTANT_REUSE) && !defined(TEST_VULKAN)
  // Only the detected constant-reuse variant may retain a clean binding.
  // Prove every referenced byte still equals the buffer's pre-packet data.
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
static void Compare(const VulkanCommandProcessor& a, const VulkanCommandProcessor& b,
                    const RegisterFile* before = nullptr) {
  Compare(static_cast<const D3D12CommandProcessor&>(a), static_cast<const D3D12CommandProcessor&>(b), before);
  assert(a.current_constant_buffers_up_to_date_ == b.current_constant_buffers_up_to_date_);
}
static void Check(uint32_t base, uint32_t count, bool repeated, unsigned wrap, bool open = true, bool truncate = false, uint32_t usage_mode = 0, bool identical = false) {
  assert(count >= 1 && count <= 16384);
  Backend a, b;
  std::mt19937 random(base + count * 71 + wrap * 1701 + usage_mode * 13);
  for (auto* p : {&a, &b}) {
    p->frame_open_ = open;
    p->regs[XE_GPU_REG_SCRATCH_ADDR] = 512;
    p->regs[XE_GPU_REG_SCRATCH_UMSK] = 0xff;
    p->regs[XE_GPU_REG_DC_LUT_WRITE_EN_MASK] = 7;
    p->regs[XE_GPU_REG_DC_LUT_RW_MODE] = base == XE_GPU_REG_DC_LUT_PWL_DATA;
  }
  for (unsigned i = 0; i < 4; ++i) {
    uint64_t v = usage_mode == 1 ? 0 : usage_mode == 2 ? UINT64_MAX : (uint64_t(random()) << 32) | random();
    uint64_t p = usage_mode == 1 ? 0 : usage_mode == 2 ? UINT64_MAX : (uint64_t(random()) << 32) | random();
    a.current_float_constant_map_vertex_[i] = b.current_float_constant_map_vertex_[i] = v;
    a.current_float_constant_map_pixel_[i] = b.current_float_constant_map_pixel_[i] = p;
  }
  unsigned capacity = count + 8;
  std::vector<uint32_t> data(capacity);
  unsigned read = wrap == 0 ? 0 : wrap == 1 ? capacity - 1 : capacity - count / 2 - 1;
  for (unsigned i = 0; i < count; ++i) data[(read + i) % capacity] = rex::byte_swap(identical ? a.regs.values[base + i] : random());
  RegisterFile before = a.regs;
  auto data_original = data;
  auto data_before = data;
  rex::memory::RingBuffer actual(reinterpret_cast<uint8_t*>(data.data()), data.size() * 4);
  rex::memory::RingBuffer original(reinterpret_cast<uint8_t*>(data_original.data()), data_original.size() * 4);
  actual.set_read_offset(read * 4); original.set_read_offset(read * 4);
  unsigned available = truncate ? count - 1 : count + 1;
  actual.set_write_offset(((read + available) % capacity) * 4);
  original.set_write_offset(((read + available) % capacity) * 4);
  uint32_t packet = ((count - 1) << 16) | (repeated ? 0x8000 : 0) | base;
  bool ok_actual = a.ExecutePacketType0(&actual, packet);
  bool ok_original = b.OriginalType0(&original, packet);
  assert(ok_actual == ok_original);
  assert(ok_actual == !truncate);
  assert(actual.read_offset() == original.read_offset());
  assert(actual.read_count() == original.read_count());
  assert(data == data_before);
  Compare(a, b, &before);
  ++cases;
}
static void Benchmark() {
  const unsigned iterations = 20000;
  for (uint32_t count : {128u, 512u, 2048u}) {
    std::vector<uint32_t> data(count + 1);
    for (uint32_t i = 0; i < count; ++i) data[i] = rex::byte_swap(0x3f000000u + i);
    for (bool original : {true, false}) {
      Backend p;
      for (unsigned i = 0; i < 4; ++i) {
        p.current_float_constant_map_vertex_[i] = UINT64_MAX;
        p.current_float_constant_map_pixel_[i] = UINT64_MAX;
      }
      rex::memory::RingBuffer ring(reinterpret_cast<uint8_t*>(data.data()), data.size() * 4);
      volatile uint32_t checksum = 0;
      const uint32_t packet = ((count - 1) << 16) | 0x4000;
      auto start = std::chrono::steady_clock::now();
      for (unsigned n = 0; n < iterations; ++n) {
        ring.set_read_offset(0); ring.set_write_offset(count * 4);
        p.cbuffer_binding_float_vertex_.up_to_date = true;
        p.cbuffer_binding_float_pixel_.up_to_date = true;
        bool ok = original ? p.OriginalType0(&ring, packet) : p.ExecutePacketType0(&ring, packet);
        assert(ok);
        checksum = checksum + p.regs.values[0x4000 + n % count];
      }
      double elapsed = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count();
      printf("MICROBENCH %s %u float dwords: %.3f us/packet, checksum %u (CPU fixture, informational only).\n",
          original ? "original" : "batched", count, elapsed / iterations, uint32_t(checksum));
    }
  }
}
int main() {
  // Packet boundaries, mixed ranges, extended registers, and ring splits at every type of constant boundary.
  const uint32_t bases[] = {0x2000, 0x3fff, 0x4000, 0x4001, 0x40ff, 0x43fd, 0x43ff, 0x4400,
    0x47ff, 0x4800, 0x4801, 0x4805, 0x48bf, 0x48ff, 0x4900, 0x4907, 0x4908, 0x4927,
    0x4928, 0x5002, 0x5003, 0x7fff};
  const uint32_t counts[] = {1, 2, 3, 4, 6, 12, 64, 256, 1024, 2048, 16384};
  for (uint32_t base : bases) for (uint32_t count : counts) for (unsigned wrap = 0; wrap < 3; ++wrap)
    for (bool repeated : {false, true}) for (bool open : {false, true})
      for (uint32_t usage = 0; usage < 3; ++usage) Check(base, count, repeated, wrap, open, false, usage);
  // Overflow refuses to consume payload or update any register.
  for (uint32_t count : counts) for (unsigned wrap = 0; wrap < 3; ++wrap)
    for (bool repeated : {false, true}) Check(0x4000, count, repeated, wrap, true, true);
  // Real scalar side effects execute via the range fallback; repeated gamma writes stay scalar.
  for (uint32_t base : {uint32_t(XE_GPU_REG_SCRATCH_REG0), uint32_t(XE_GPU_REG_COHER_STATUS_HOST),
      uint32_t(XE_GPU_REG_DC_LUT_RW_INDEX), uint32_t(XE_GPU_REG_DC_LUT_SEQ_COLOR),
      uint32_t(XE_GPU_REG_DC_LUT_PWL_DATA), uint32_t(XE_GPU_REG_DC_LUT_30_COLOR)}) {
    for (unsigned wrap = 0; wrap < 3; ++wrap) {
      Check(base, 1, false, wrap);
      Check(base, 12, true, wrap);
    }
  }
#ifdef TEST_CONSTANT_REUSE
  for (uint32_t base : {0x4000u, 0x4001u, 0x43ffu, 0x4400u, 0x4900u, 0x4907u})
    for (uint32_t count : {1u, 4u, 8u, 32u}) for (unsigned wrap = 0; wrap < 3; ++wrap)
      for (uint32_t usage = 0; usage < 3; ++usage)
        Check(base, count, false, wrap, true, false, usage, true);
#endif
  Benchmark();
#ifdef TEST_VULKAN
  puts("PASS Vulkan: exact patched TYPE0, original scalar TYPE0, real RingBuffer and bulk/scalar methods agree.");
#else
  puts("PASS D3D12: exact patched TYPE0, original scalar TYPE0, real RingBuffer and bulk/scalar methods agree.");
#endif
  printf("%u boundary/matrix cases; register bytes, dirty CB/fetch/residency state, gamma, scratch, coherency, and ring offsets verified.\n", cases);
}
'''
source_file = output / 'type0_regression.cpp'
source_file.write_text(prefix + base_methods + old_method + d3d_methods + vulkan_methods + suffix)
executable = output / 'type0_regression.exe'
sources = [source_file, sdk / 'src/core/ring_buffer.cpp', sdk / 'src/graphics/register_file.cpp']
includes = [installed / 'include', sdk / 'include']
# cl-compatible drivers need Microsoft-style arguments; clang++ accepts GNU-style
# arguments with the Microsoft runtime selected. Compile entirely in output.
cl_style = Path(compiler).name.lower() in {'cl', 'cl.exe', 'clang-cl', 'clang-cl.exe'}
if cl_style:
    command = [compiler, '/nologo', '/MD', '/EHsc', '/O2', '/std:c++latest',
               '/DSPDLOG_COMPILED_LIB', '/DSPDLOG_FMT_EXTERNAL']
    if 'clang' in Path(compiler).name.lower(): command += ['/clang:-march=x86-64-v2']
    command += ['/I' + str(p) for p in includes]
    command += [str(p) for p in sources]
    command += ['/Fo' + str(output) + os.sep, '/Fe' + str(executable), '/link', str(installed / 'lib/rexruntime.lib')]
else:
    command = [compiler, '-std=c++23', '-O2', '-march=x86-64-v2', '-fms-runtime-lib=dll',
               '-Xlinker', '/NODEFAULTLIB:libcmt',
               '-DSPDLOG_COMPILED_LIB', '-DSPDLOG_FMT_EXTERNAL']
    command += ['-I' + str(p) for p in includes]
    command += [str(p) for p in sources]
    command += [str(installed / 'lib/rexruntime.lib'), '-o', str(executable)]
environment = os.environ.copy()
environment['PATH'] = str(installed / 'bin') + os.pathsep + environment.get('PATH', '')
if 'copy_range_and_check_changes' in d3d_methods:
    command.insert(1, '/DTEST_CONSTANT_REUSE' if cl_style else '-DTEST_CONSTANT_REUSE')
for backend in ('D3D12', 'Vulkan'):
    compile_command = command.copy()
    if backend == 'Vulkan':
        compile_command.insert(1, '/DTEST_VULKAN' if cl_style else '-DTEST_VULKAN')
    print('Checking', backend, 'TYPE0 boundaries...', flush=True)
    subprocess.run(compile_command, cwd=output, check=True)
    subprocess.run([str(executable)], cwd=output, env=environment, check=True)
if args.output: print('Native regression artifacts:', output, flush=True)

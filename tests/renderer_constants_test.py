"""Differential-check the SDK's D3D12 float constant gathering loops.

Compiles the exact patched loops and the source worktree's git HEAD loops,
using real RegisterFile storage and the installed runtime's bit_scan_forward.
No graphics device is created. Run in a Visual Studio developer environment.
Microbenchmark timings are informational and are never pass/fail criteria.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True, type=Path, help='Patched ReXGlue source git worktree')
parser.add_argument('--sdk', required=True, type=Path, help='Matching installed Windows SDK root')
parser.add_argument('--cxx', required=True, help='clang++, clang-cl or cl compiler')
parser.add_argument('--output', type=Path, help='Keep generated fixture and executable here')
args = parser.parse_args()
source_root = args.source.resolve(strict=True)
installed = args.sdk.resolve(strict=True)
compiler = shutil.which(args.cxx) or str(Path(args.cxx).resolve(strict=True))
temporary = tempfile.TemporaryDirectory(prefix='renderer-constants-') if args.output is None else None
output = Path(temporary.name) if temporary else args.output.resolve()
output.mkdir(parents=True, exist_ok=True)

relative = 'src/graphics/d3d12/command_processor.cpp'
patched = (source_root / relative).read_text()
original = subprocess.check_output(['git', 'show', 'HEAD:' + relative], cwd=source_root, text=True)


def gather_loop(text, bank):
    """Extract the loop after allocation, rather than shader-map dirty tracking."""
    function_start = text.index('bool D3D12CommandProcessor::UpdateBindings(')
    anchor = text.index(f'uint64_t float_constant_map_entry = float_constant_map_{bank}.float_bitmap[i];',
                        function_start)
    start = text.rindex('for (uint32_t i = 0; i < 4; ++i)', function_start, anchor)
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        if text[end] == '{':
            depth += 1
        elif text[end] == '}':
            depth -= 1
        end += 1
    return text[start:end]


methods = []
for bank in ('vertex', 'pixel'):
    for label, text in (('Original', original), ('Patched', patched)):
        loop = gather_loop(text, bank)
        if label == 'Patched' and 'std::countr_zero' not in loop:
            parser.error('--source must contain the local constant bitmap scan patch')
        if label == 'Original' and 'rex::bit_scan_forward' not in loop:
            parser.error('git HEAD must contain the original exported bitmap scan baseline')
        methods.append(f'''
__declspec(noinline) uint8_t* {label}{bank}(uint8_t* float_constants,
    const rex::graphics::RegisterFile& regs, const ConstantMap& float_constant_map_{bank}) {{
  {loop}
  return float_constants;
}}
''')

prefix = r'''
#include <array>
#include <bit>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>
#include <rex/graphics/register_file.h>
#include <rex/math.h>
#ifdef NDEBUG
#error Constant packing regression assertions must remain enabled.
#endif
using namespace rex::graphics;
struct ConstantMap { uint64_t float_bitmap[4]{}; };
'''

suffix = r'''
using Gather = uint8_t* (*)(uint8_t*, const RegisterFile&, const ConstantMap&);
static unsigned cases = 0;
static void Check(const RegisterFile& regs, const ConstantMap& map, unsigned offset) {
  constexpr size_t size = 4096 + 64;
  const std::array<uint32_t, 2> bases = {
      XE_GPU_REG_SHADER_CONSTANT_000_X, XE_GPU_REG_SHADER_CONSTANT_256_X};
  for (unsigned bank = 0; bank < 2; ++bank) {
    std::array<uint8_t, size> expected, actual, baseline;
    expected.fill(0xcd); actual.fill(0xcd); baseline.fill(0xcd);
    uint8_t* cursor = expected.data() + offset;
    // Independent scalar oracle, including arbitrary NaN/Inf payload bits.
    for (unsigned index = 0; index < 256; ++index) {
      if ((map.float_bitmap[index / 64] >> (index % 64)) & 1) {
        memcpy(cursor, &regs.values[bases[bank] + index * 4], 16);
        cursor += 16;
      }
    }
    Gather old_gather = bank ? Originalpixel : Originalvertex;
    Gather new_gather = bank ? Patchedpixel : Patchedvertex;
    uint8_t* old_end = old_gather(baseline.data() + offset, regs, map);
    uint8_t* new_end = new_gather(actual.data() + offset, regs, map);
    assert(old_end - baseline.data() == cursor - expected.data());
    assert(new_end - actual.data() == cursor - expected.data());
    assert(baseline == expected);
    assert(actual == expected);  // Verifies ordering, exact bytes and both guards.
    ++cases;
  }
}
static void Benchmark(const RegisterFile& regs) {
  const std::array<ConstantMap, 6> patterns = {{
    {{0, 0, 0, 0}},
    {{1, 0, 0, 1ull << 63}},
    {{0xffff, 0, 0, 0}},
    {{0x8000000000000001ull, 0x10, 0x8000, 0x100000000ull}},
    {{0x5555555555555555ull, 0xaaaaaaaaaaaaaaaaull, 0x5555555555555555ull, 0xaaaaaaaaaaaaaaaaull}},
    {{UINT64_MAX, UINT64_MAX, UINT64_MAX, UINT64_MAX}},
  }};
  const char* names[] = {"empty", "two distant", "16 consecutive", "five sparse", "alternating", "all 256"};
  constexpr unsigned iterations = 100000;
  std::array<uint8_t, 4096> destination{};
  // Alternate baseline/patched ordering across patterns. Actual runtime scan
  // calls remain out of line; local loops are noinline only at the fixture edge.
  for (unsigned pattern = 0; pattern < patterns.size(); ++pattern) {
    for (unsigned order = 0; order < 2; ++order) {
      bool patched = (order ^ (pattern & 1)) != 0;
      Gather gather = patched ? Patchedvertex : Originalvertex;
      volatile uint32_t checksum = 0;
      auto start = std::chrono::steady_clock::now();
      for (unsigned i = 0; i < iterations; ++i) {
        auto* end = gather(destination.data(), regs, patterns[pattern]);
        checksum = checksum + uint32_t(end - destination.data()) + destination[i % destination.size()];
      }
      double elapsed = std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - start).count();
      printf("MICROBENCH %s %s: %.2f ns/gather, checksum %u (informational CPU fixture).\n",
          patched ? "patched" : "original", names[pattern], elapsed / iterations, uint32_t(checksum));
    }
  }
}
int main() {
  RegisterFile regs;
  std::mt19937_64 random(0x746170636f6e7374ull);
  for (auto& value : regs.values) value = uint32_t(random());
  // Include floating-point bit patterns which must never be interpreted or canonicalized.
  for (unsigned bank : {0x4000u, 0x4400u}) {
    for (unsigned i = 0; i < 256; ++i) {
      regs.values[bank + i * 4] = 0x7fc00000u | i;   // NaN payload
      regs.values[bank + i * 4 + 1] = 0x80000000u;  // negative zero
      regs.values[bank + i * 4 + 2] = 0xff800000u;  // -infinity
    }
  }
  std::array<uint32_t, RegisterFile::kRegisterCount> before;
  memcpy(before.data(), regs.values, sizeof(regs.values));
  ConstantMap map;
  for (unsigned offset : {0u, 1u, 3u, 8u, 15u, 31u}) {
    Check(regs, map, offset);
    for (unsigned index = 0; index < 256; ++index) {
      map = {}; map.float_bitmap[index / 64] = 1ull << (index % 64);
      Check(regs, map, offset);
    }
    for (uint64_t bits : {0ull, 1ull, 0x8000000000000000ull, 0x5555555555555555ull,
                         0xaaaaaaaaaaaaaaaaull, UINT64_MAX}) {
      for (auto& word : map.float_bitmap) word = bits;
      Check(regs, map, offset);
    }
    for (unsigned boundary : {63u, 127u, 191u}) {
      map = {};
      map.float_bitmap[boundary / 64] = 1ull << (boundary % 64);
      map.float_bitmap[(boundary + 1) / 64] |= 1ull << ((boundary + 1) % 64);
      Check(regs, map, offset);
    }
  }
  for (unsigned i = 0; i < 10000; ++i) {
    for (auto& word : map.float_bitmap) word = random();
    Check(regs, map, i % 32);
  }
  assert(!memcmp(before.data(), regs.values, sizeof(regs.values)));
  printf("PASS: %u original/patched/oracle comparisons across VS/PS banks, bitmap boundaries, unaligned destinations and NaN payloads; source registers unchanged.\n", cases);
  Benchmark(regs);
}
'''

fixture = output / 'renderer_constants_test.cpp'
fixture.write_text(prefix + '\n'.join(methods) + suffix)
executable = output / 'renderer_constants_test.exe'
sources = [fixture, source_root / 'src/graphics/register_file.cpp']
includes = [installed / 'include', source_root / 'include']
cl_style = Path(compiler).name.lower() in {'cl', 'cl.exe', 'clang-cl', 'clang-cl.exe'}
if cl_style:
    command = [compiler, '/nologo', '/MD', '/EHsc', '/O2', '/std:c++latest',
               '/DSPDLOG_COMPILED_LIB', '/DSPDLOG_FMT_EXTERNAL']
    if 'clang' in Path(compiler).name.lower():
        command += ['/clang:-msse4.1']
    command += ['/I' + str(path) for path in includes]
    command += [str(path) for path in sources]
    command += ['/Fo' + str(output) + os.sep, '/Fe' + str(executable),
                '/link', str(installed / 'lib/rexruntime.lib')]
else:
    command = [compiler, '-std=c++23', '-O2', '-msse4.1', '-fms-runtime-lib=dll',
               '-Xlinker', '/NODEFAULTLIB:libcmt', '-DSPDLOG_COMPILED_LIB', '-DSPDLOG_FMT_EXTERNAL']
    command += ['-I' + str(path) for path in includes]
    command += [str(path) for path in sources]
    command += [str(installed / 'lib/rexruntime.lib'), '-o', str(executable)]
subprocess.run(command, cwd=output, check=True)
environment = os.environ.copy()
environment['PATH'] = str(installed / 'bin') + os.pathsep + environment.get('PATH', '')
subprocess.run([str(executable)], cwd=output, env=environment, check=True)
if args.output:
    print('Native constant packing test artifacts:', output, flush=True)

"""Check actual old/new D3D12 bulk constant methods without a GPU.

The portable x86 C++23 fixture compiles SDK method bodies and SIMD copy helpers.
GPU bindings, scalar base writes and cache callbacks are observable fixtures.
An independent word/range oracle verifies dirty flags; packed bytes must match
even when the new path retains an existing buffer. Timings are informational.
Combined control batching uses an explicit debug/null logger facade; real logger
messages and base side effects are independently checked by renderer_control_test.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def extract(text, signature):
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        if text[end] == '{':
            depth += 1
        elif text[end] == '}':
            depth -= 1
        end += 1
    return text[start:end] + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--sdk', required=True, type=Path)
    parser.add_argument('--cxx', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    sdk = args.sdk.resolve(strict=True)
    compiler = shutil.which(args.cxx) or str(Path(args.cxx).resolve(strict=True))
    path = 'src/graphics/d3d12/command_processor.cpp'
    current = (source / path).read_text(encoding='utf-8')
    original = subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=source, text=True)
    signature = 'void D3D12CommandProcessor::WriteRegistersFromMem('
    old = extract(original, signature)
    new = extract(current, signature)
    if 'copy_range_and_check_changes' not in new:
        parser.error('--source must contain the constant reuse patch')
    # Fetch callbacks and the base scalar fallback must be exactly unchanged.
    marker = '  if (start_index >= XE_GPU_REG_SHADER_CONSTANT_FETCH_00_0'
    assert new.split(marker, 1)[1] == old.split(marker, 1)[1]
    scalar = 'void D3D12CommandProcessor::WriteRegister('
    assert extract(current, scalar) == extract(original, scalar)
    methods = new + old.replace('::WriteRegistersFromMem(', '::Original(', 1)
    # Benchmark a second verbatim method without the simulated-mutation hook.
    methods += '#undef _mm_loadu_si128\n'
    methods += new.replace('::WriteRegistersFromMem(', '::BenchmarkReuse(', 1)
    for sig in [scalar,
                'void D3D12CommandProcessor::InvalidateVertexBufferResidency(',
                'void D3D12CommandProcessor::InvalidateVertexBufferResidencyRange(']:
        methods += extract(current, sig)
    # Also compile the exact current float pack loop for the microbenchmark.
    anchor = current.index('uint64_t float_constant_map_entry = float_constant_map_vertex.float_bitmap[i];',
                           current.index('bool D3D12CommandProcessor::UpdateBindings('))
    loop_start = current.rfind('for (uint32_t i = 0; i < 4; ++i)', 0, anchor)
    loop = extract(current[loop_start:], 'for (uint32_t i = 0; i < 4; ++i)')
    methods += 'struct ConstantMap { uint64_t float_bitmap[4]{}; };\n'
    methods += 'void BenchGather(uint8_t* float_constants,const RegisterFile& regs,const ConstantMap& float_constant_map_vertex) {\n' + loop + '}\n'
    layout_start = current.index('  // Check if the float constant layout is still the same',
                                 current.index('bool D3D12CommandProcessor::UpdateBindings('))
    layout_end = current.index('  // Write the constant buffer data.', layout_start)
    methods += 'void D3D12CommandProcessor::Layout(const FixtureShader* vertex_shader,const FixtureShader* pixel_shader) {\n'
    methods += current[layout_start:layout_end] + '}\n'
    copies = (source / 'src/core/memory.cpp').read_text(encoding='utf-8')
    memory = 'namespace rex::memory {\n'
    memory += extract(copies, 'void copy_and_swap_32_aligned(')
    memory += extract(copies, 'void copy_and_swap_32_unaligned(')
    memory += MEMORY + '}\n'
    temporary = tempfile.TemporaryDirectory(prefix='renderer-reuse-') if args.output is None else None
    output = Path(temporary.name) if temporary else args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cpp = output / 'constant_reuse_fixture.cpp'
    exe = output / ('constant_reuse_fixture.exe' if os.name == 'nt' else 'constant_reuse_fixture')
    flags = '#define TEST_CONTROL_BATCHING\n' if 'num_registers <= 0x4000 - start_index' in new else ''
    cpp.write_text(flags + PREFIX + memory + VECTOR_LOAD + LOGGER + FIXTURE + methods + SUFFIX,
                   encoding='utf-8')
    name = Path(compiler).name.lower()
    if name in {'cl', 'cl.exe', 'clang-cl', 'clang-cl.exe'}:
        command = [compiler, '/nologo', '/std:c++latest', '/O2', '/EHsc',
                   '/I' + str(sdk / 'include'), str(cpp), '/Fe:' + str(exe)]
        if 'clang' in name:
            command += ['/clang:-march=x86-64-v2']
    else:
        command = [compiler, '-std=c++23', '-O2', '-march=x86-64-v2', '-I', str(sdk / 'include'),
                   str(cpp), '-o', str(exe)]
    subprocess.run(command, cwd=output, check=True)
    subprocess.run([str(exe)], cwd=output, check=True, timeout=60)
    if temporary:
        temporary.cleanup()


PREFIX = r'''
#include <algorithm>
#include <array>
#include <bit>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <random>
#include <utility>
#include <vector>
#include <immintrin.h>
#ifdef NDEBUG
#error Regression assertions must remain enabled.
#endif
#define assert_zero(value) assert(!(value))
#define REX_WORKAROUND_CONSTANT_RETURN_IF(condition) if (condition) return
template <typename T> T byte_swap(T value) { return std::byteswap(value); }
'''

MEMORY = r'''
static uint32_t* mutate_after_read=nullptr;
static uint32_t mutated_guest_word=0;
static unsigned observed_reads=0;
template <typename T> T load_and_swap(const void* source) {
  T value;std::memcpy(&value,source,sizeof(value));
  if(source==mutate_after_read) {
    ++observed_reads;*mutate_after_read=mutated_guest_word;mutate_after_read=nullptr;
  }
  return byte_swap(value);
}
template <typename T> void copy_and_swap(T* dest,const T* source,size_t count) {
  static_assert(sizeof(T)==4);
  if ((reinterpret_cast<uintptr_t>(dest)|reinterpret_cast<uintptr_t>(source))%32==0)
    copy_and_swap_32_aligned(dest,source,count);
  else copy_and_swap_32_unaligned(dest,source,count);
}
'''

VECTOR_LOAD = r'''
static __m128i FixtureLoad128(const __m128i* source) {
  __m128i value=_mm_loadu_si128(source);
  if(reinterpret_cast<const void*>(source)==rex::memory::mutate_after_read) {
    ++rex::memory::observed_reads;
    *rex::memory::mutate_after_read=rex::memory::mutated_guest_word;
    rex::memory::mutate_after_read=nullptr;
  }
  return value;
}
// Only the extracted new method uses this wrapper; the exact SIMD copy
// helpers above are unchanged. It models source mutation just after a load.
#define _mm_loadu_si128 FixtureLoad128
'''

LOGGER = r'''
// Explicit logger facade for the optional control branch. The separate
// control fixture verifies real runtime logging; here we preserve exactly
// should_log(debug), category selection and absent-logger behavior.
namespace spdlog::level { enum level_enum { debug }; }
namespace rex {
struct FixtureLogger {
  bool debug_enabled=false;unsigned checks=0;
  bool should_log(spdlog::level::level_enum level) {
    assert(level==spdlog::level::debug);++checks;return debug_enabled;
  }
};
inline FixtureLogger fixture_logger;
inline bool fixture_logger_present=true;
inline unsigned logger_lookups=0;
namespace log { inline unsigned gpu() { return 1; } }
inline FixtureLogger* GetLoggerRaw(unsigned category) {
  assert(category==log::gpu());++logger_lookups;
  return fixture_logger_present ? &fixture_logger : nullptr;
}
}
'''

FIXTURE = r'''
namespace rex::graphics {
enum Register {
#define XE_GPU_REGISTER(index,type,name) XE_GPU_REG_##name=index,
#include <rex/graphics/register_table.inc>
#undef XE_GPU_REGISTER
};
struct Words : std::array<uint32_t,0x5003> {
  operator uint32_t*() { return data(); }
};
struct RegisterFile {
  Words values{};
  const uint32_t& operator[](uint32_t index) const { return values[index]; }
};
class CommandProcessor {
 public:
  RegisterFile storage;
  RegisterFile* register_file_=&storage;
  std::vector<std::pair<uint32_t,uint32_t>> scalar_writes;
  virtual void WriteRegister(uint32_t index,uint32_t value) {
    assert(index<storage.values.size());storage.values[index]=value;
    scalar_writes.emplace_back(index,value);
  }
  void WriteRegistersFromMem(uint32_t start,uint32_t* source,uint32_t count) {
    for(uint32_t i=0;i<count;++i)WriteRegister(start+i,memory::load_and_swap<uint32_t>(source+i));
  }
};
struct Texture {
  uint32_t dirty=0;unsigned calls=0;
  void TextureFetchConstantWritten(uint32_t index) { TextureFetchConstantsWritten(index,index); }
  void TextureFetchConstantsWritten(uint32_t first,uint32_t last) {
    ++calls;for(uint32_t i=first;i<=last;++i)dirty|=uint32_t(1)<<i;
  }
};
struct Shader {
  struct ConstantRegisterMap { uint64_t float_bitmap[4]{};uint32_t float_count=0; };
};
struct FixtureShader {
  Shader::ConstantRegisterMap map;
  const Shader::ConstantRegisterMap& constant_register_map() const { return map; }
};
class D3D12CommandProcessor : public CommandProcessor {
 public:
  bool frame_open_=true;
  uint64_t current_float_constant_map_vertex_[4]{};
  uint64_t current_float_constant_map_pixel_[4]{};
  struct Binding { bool up_to_date=true;std::vector<uint32_t> bytes;unsigned uploads=0; };
  Binding cbuffer_binding_float_vertex_,cbuffer_binding_float_pixel_;
  Binding cbuffer_binding_bool_loop_,cbuffer_binding_fetch_;
  Texture texture;
  Texture* texture_cache_=&texture;
  std::array<uint32_t,96> vertex_buffer_states_{};
  uint64_t vertex_buffers_in_sync_[2]={UINT64_MAX,UINT64_MAX};
  void WriteRegister(uint32_t,uint32_t) override;
  void WriteRegistersFromMem(uint32_t,uint32_t*,uint32_t);
  void Original(uint32_t,uint32_t*,uint32_t);
  void BenchmarkReuse(uint32_t,uint32_t*,uint32_t);
  void Layout(const FixtureShader*,const FixtureShader*);
  void InvalidateVertexBufferResidency(uint32_t);
  void InvalidateVertexBufferResidencyRange(uint32_t,uint32_t);
};
'''

SUFFIX = r'''
}
using namespace rex::graphics;
namespace memory=rex::memory;
using CP=D3D12CommandProcessor;
static unsigned cases=0,reused=0;
static constexpr uint32_t FB=XE_GPU_REG_SHADER_CONSTANT_000_X;
static constexpr uint32_t PB=XE_GPU_REG_SHADER_CONSTANT_256_X;
static constexpr uint32_t BB=XE_GPU_REG_SHADER_CONSTANT_BOOL_000_031;
static constexpr uint32_t FE=XE_GPU_REG_SHADER_CONSTANT_511_W;
static constexpr uint32_t BE=XE_GPU_REG_SHADER_CONSTANT_LOOP_31;
static std::vector<uint32_t> Pack(const CP& p,unsigned bank) {
  std::vector<uint32_t> result;
  if(bank<2) {
    const uint64_t* map=bank ? p.current_float_constant_map_pixel_ : p.current_float_constant_map_vertex_;
    for(unsigned i=0;i<256;++i)if((map[i/64]>>(i%64))&1)
      for(unsigned j=0;j<4;++j)result.push_back(p.storage.values[FB+bank*1024+i*4+j]);
  } else {
    uint32_t start=bank==2 ? BB : uint32_t(XE_GPU_REG_SHADER_CONSTANT_FETCH_00_0);
    unsigned count=bank==2 ? 40 : 192;
    result.assign(p.storage.values.begin()+start,p.storage.values.begin()+start+count);
  }
  return result;
}
static CP::Binding& Binding(CP& p,unsigned i) {
  if(i==0)return p.cbuffer_binding_float_vertex_;
  if(i==1)return p.cbuffer_binding_float_pixel_;
  if(i==2)return p.cbuffer_binding_bool_loop_;
  return p.cbuffer_binding_fetch_;
}
static void Check(uint32_t start,uint32_t count,unsigned pattern,unsigned usage,
                  bool open,bool dirty,unsigned offset,bool no_texture=false,
                  bool debug=false,bool logger_present=true) {
  CP a,b;
  a.frame_open_=b.frame_open_=open;
  rex::fixture_logger.debug_enabled=debug;rex::fixture_logger_present=logger_present;
  rex::fixture_logger.checks=0;rex::logger_lookups=0;
  if(no_texture)a.texture_cache_=b.texture_cache_=nullptr;
  for(unsigned i=0;i<a.storage.values.size();++i)a.storage.values[i]=b.storage.values[i]=0x01234567u^(i*0xABCDEu);
  for(unsigned i=0;i<4;++i) {
    uint64_t v=usage==0 ? 0 : usage==1 ? UINT64_MAX : UINT64_C(0x8000000180000001);
    a.current_float_constant_map_vertex_[i]=b.current_float_constant_map_vertex_[i]=v;
    a.current_float_constant_map_pixel_[i]=b.current_float_constant_map_pixel_[i]=std::rotl(v,17);
  }
  for(unsigned i=0;i<4;++i) {
    Binding(a,i).bytes=Binding(b,i).bytes=Pack(a,i);
    Binding(a,i).up_to_date=Binding(b,i).up_to_date=!dirty;
  }
  std::vector<uint32_t> host(count), storage(count+offset+8,0xCDDCCDDCu);
  uint32_t* guest=storage.data()+offset;
  for(unsigned i=0;i<count;++i) {
    uint32_t value=a.storage.values[start+i];
    if(pattern==1 || (pattern==2 && i==0) || (pattern==3 && i+1==count) ||
       (pattern==4 && i==count/2))value^=0x80000000u;
    if(pattern==5)value=0x7FA01234u+i; // Distinct signalling/quiet NaN payloads.
    host[i]=value;guest[i]=std::byteswap(value);
  }
  auto before=a.storage.values;
  bool expected[4]={!dirty,!dirty,!dirty,!dirty};
  if(count) {
    uint32_t end=start+count-1;
    if(start>=FB && end<=FE) {
      if(open)for(unsigned bank=0;bank<2;++bank) {
        uint32_t lo=std::max(start,FB+bank*1024),hi=std::min(end,FB+bank*1024+1023);
        if(lo>hi)continue;
        const uint64_t* map=bank ? a.current_float_constant_map_pixel_ : a.current_float_constant_map_vertex_;
        bool referenced=false,changed=false;
        for(uint32_t index=lo;index<=hi;++index) {
          unsigned c=(index-FB-bank*1024)/4;
          referenced|=((map[c/64]>>(c%64))&1)!=0;
          changed|=before[index]!=host[index-start];
        }
        if(referenced && changed)expected[bank]=false;
      }
    } else if(start>=BB && end<=BE) {
      for(unsigned i=0;i<count;++i)if(before[start+i]!=host[i])expected[2]=false;
    } else {
      // Fallback and fetch callbacks must preserve original dirty state exactly.
      expected[0]=expected[1]=expected[2]=expected[3]=false; // Replaced below after baseline.
    }
  }
  b.Original(start,guest,count);a.WriteRegistersFromMem(start,guest,count);
  assert(a.storage.values==b.storage.values);
  for(unsigned i=0;i<count;++i)assert(a.storage.values[start+i]==host[i]);
#ifdef TEST_CONTROL_BATCHING
  bool control=count && start>=0x2000 && start<0x4000 && count<=0x4000-start;
  if(control && (!logger_present || !debug)) {
    // The audited fast branch omits fixture scalar-call bookkeeping, while
    // the exact final words and all GPU side-effect state still agree.
    assert(a.scalar_writes.empty() && b.scalar_writes.size()==count);
  } else assert(a.scalar_writes==b.scalar_writes);
  assert(rex::logger_lookups==(control ? 1u : 0u));
  assert(rex::fixture_logger.checks==(control && logger_present ? 1u : 0u));
#else
  assert(a.scalar_writes==b.scalar_writes);
  assert(!rex::logger_lookups && !rex::fixture_logger.checks);
#endif
  assert(a.texture.calls==b.texture.calls && a.texture.dirty==b.texture.dirty);
  assert(a.vertex_buffers_in_sync_[0]==b.vertex_buffers_in_sync_[0]);
  assert(a.vertex_buffers_in_sync_[1]==b.vertex_buffers_in_sync_[1]);
  bool contained=!count || (start>=FB && start+count-1<=FE) || (start>=BB && start+count-1<=BE);
  for(unsigned i=0;i<4;++i) {
    if(!contained || i==3)expected[i]=Binding(b,i).up_to_date;
    assert(Binding(a,i).up_to_date==expected[i]);
    if(Binding(a,i).up_to_date && !Binding(b,i).up_to_date)++reused;
    // Simulate allocation/packing at UpdateBindings, preserving clean buffers.
    // Frame-closed float registers are invalidated at BeginFrame, as in SDK.
    if(!open && i<2)continue;
    for(CP* p:{&a,&b})if(!Binding(*p,i).up_to_date)Binding(*p,i).bytes=Pack(*p,i);
    assert(Binding(a,i).bytes==Binding(b,i).bytes);
  }
  for(unsigned i=0;i<offset;++i)assert(storage[i]==0xCDDCCDDCu);
  for(unsigned i=offset+count;i<storage.size();++i)assert(storage[i]==0xCDDCCDDCu);
  ++cases;
}
static void Lifecycle() {
  CP p;p.current_float_constant_map_vertex_[0]=1;
  uint32_t word=std::byteswap(uint32_t(0));
  p.WriteRegistersFromMem(FB,&word,1);assert(p.cbuffer_binding_float_vertex_.up_to_date);
  // Scalar writes retain original dirtiness even with identical data.
  p.WriteRegister(FB,0);assert(!p.cbuffer_binding_float_vertex_.up_to_date);
  // A shader layout change / new frame already dirty must remain dirty.
  p.WriteRegistersFromMem(FB,&word,1);assert(!p.cbuffer_binding_float_vertex_.up_to_date);
  // Actual SDK UpdateBindings shader-map tracking must invalidate a reused
  // packed buffer when the shader's layout changes, even without new writes.
  FixtureShader vertex,pixel;vertex.map.float_bitmap[0]=1;vertex.map.float_count=1;
  pixel.map.float_bitmap[0]=1;pixel.map.float_count=1;
  p.cbuffer_binding_float_vertex_.up_to_date=true;p.Layout(&vertex,&pixel);
  assert(p.cbuffer_binding_float_vertex_.up_to_date);
  p.cbuffer_binding_float_pixel_.up_to_date=true;
  vertex.map.float_bitmap[0]=2;pixel.map.float_bitmap[0]=2;
  p.Layout(&vertex,&pixel);
  assert(!p.cbuffer_binding_float_vertex_.up_to_date && !p.cbuffer_binding_float_pixel_.up_to_date);
  p.cbuffer_binding_float_pixel_.up_to_date=true;p.Layout(&vertex,nullptr);
  assert(p.current_float_constant_map_pixel_[0]==0);
  p.Layout(&vertex,&pixel);assert(!p.cbuffer_binding_float_pixel_.up_to_date);
  p.cbuffer_binding_float_vertex_.up_to_date=true;
  p.frame_open_=false;word=std::byteswap(uint32_t(0x80000000));
  p.WriteRegistersFromMem(FB,&word,1);assert(p.cbuffer_binding_float_vertex_.up_to_date);
  p.frame_open_=true;p.cbuffer_binding_float_vertex_.up_to_date=false;
  p.WriteRegistersFromMem(FB,&word,1);assert(!p.cbuffer_binding_float_vertex_.up_to_date);
  // Vertex/pixel split: only changed pixel bank invalidates; crossing write retained vertex.
  CP q;q.current_float_constant_map_vertex_[3]=uint64_t(1)<<63;q.current_float_constant_map_pixel_[0]=1;
  uint32_t split[]={0,std::byteswap(uint32_t(1))};
  q.WriteRegistersFromMem(PB-1,split,2);
  assert(q.cbuffer_binding_float_vertex_.up_to_date && !q.cbuffer_binding_float_pixel_.up_to_date);
  // Bit-exact NaNs / signed zero: same NaN retained, payload / sign changes dirty.
  for(uint32_t value:{0x7FA01234u,0x7FC01234u,0x80000000u,0u}) {
    q.storage.values[FB]=value;q.current_float_constant_map_vertex_[0]=1;
    q.cbuffer_binding_float_vertex_.up_to_date=true;word=std::byteswap(value);
    q.WriteRegistersFromMem(FB,&word,1);assert(q.cbuffer_binding_float_vertex_.up_to_date);
    word^=std::byteswap(uint32_t(1));q.WriteRegistersFromMem(FB,&word,1);
    assert(!q.cbuffer_binding_float_vertex_.up_to_date);
  }
  // Source changes immediately after the load. A pre-compare + second copy
  // would store the new word while incorrectly keeping the old CB clean.
  for(uint32_t index:{FB,PB,BB,BE}) {
    CP r;r.current_float_constant_map_vertex_[0]=1;r.current_float_constant_map_pixel_[0]=1;
    uint32_t source=0;memory::observed_reads=0;
    memory::mutate_after_read=&source;memory::mutated_guest_word=std::byteswap(uint32_t(0x7FA01234));
    r.WriteRegistersFromMem(index,&source,1);
    assert(r.storage.values[index]==0 && memory::observed_reads==1);
    unsigned bank=index>=BB ? 2 : index>=PB ? 1 : 0;
    assert(Binding(r,bank).up_to_date);
    r.WriteRegistersFromMem(index,&source,1);
    assert(r.storage.values[index]==0x7FA01234 && !Binding(r,bank).up_to_date);
  }
  for(uint32_t index:{FB,PB,BB})for(unsigned offset=0;offset<4;++offset)
      for(unsigned count:{4u,5u,7u,8u}) {
    CP r;r.current_float_constant_map_vertex_[0]=3;r.current_float_constant_map_pixel_[0]=3;
    std::array<uint32_t,12> storage{};uint32_t* source=storage.data()+offset;
    memory::observed_reads=0;memory::mutate_after_read=source;
    memory::mutated_guest_word=std::byteswap(uint32_t(0x80000000));
    r.WriteRegistersFromMem(index,source,count);
    assert(memory::observed_reads==1);
    for(unsigned i=0;i<count;++i)assert(r.storage.values[index+i]==0);
    unsigned bank=index>=BB ? 2 : index>=PB ? 1 : 0;
    assert(Binding(r,bank).up_to_date);
    r.WriteRegistersFromMem(index,source,count);
    assert(r.storage.values[index]==0x80000000 && !Binding(r,bank).up_to_date);
  }
  for(uint32_t index:{FB,PB})for(unsigned lane=0;lane<4;++lane) {
    CP r;r.current_float_constant_map_vertex_[0]=1;r.current_float_constant_map_pixel_[0]=1;
    std::array<uint32_t,4> payload={0x7FA01234u,0x7FC04321u,0x80000000u,0x7F800000u};
    std::array<uint32_t,4> source;
    for(unsigned i=0;i<4;++i) {
      r.storage.values[index+i]=payload[i];source[i]=std::byteswap(payload[i]);
    }
    unsigned bank=index>=PB ? 1 : 0;
    r.WriteRegistersFromMem(index,source.data(),4);assert(Binding(r,bank).up_to_date);
    source[lane]^=std::byteswap(uint32_t(1));
    r.WriteRegistersFromMem(index,source.data(),4);assert(!Binding(r,bank).up_to_date);
    for(unsigned i=0;i<4;++i)assert(r.storage.values[index+i]==(payload[i]^(i==lane ? 1u : 0u)));
  }
}
static void Benchmark() {
  constexpr unsigned iterations=200000;
  for(uint32_t count:{4u,40u,64u,256u,1024u})for(unsigned change:{0u,1u,2u})for(bool old:{true,false}) {
    CP p;ConstantMap map;
    for(unsigned i=0;i<std::max(1u,count/4);++i) {
      p.current_float_constant_map_vertex_[i/64]|=uint64_t(1)<<(i%64);
      map.float_bitmap[i/64]|=uint64_t(1)<<(i%64);
    }
    uint32_t start=count==40 ? BB : FB;
    std::vector<uint32_t> data(count,0);
    auto& target=count==40 ? p.cbuffer_binding_bool_loop_ : p.cbuffer_binding_float_vertex_;
    target.bytes.resize(count==40 ? 40 : std::max(4u,count));
    unsigned uploads=0;volatile uint32_t checksum=0;
    auto began=std::chrono::steady_clock::now();
    for(unsigned n=0;n<iterations;++n) {
      auto& binding=count==40 ? p.cbuffer_binding_bool_loop_ : p.cbuffer_binding_float_vertex_;
      binding.up_to_date=true;
      if(change)data[change==1 ? 0 : count-1]=std::byteswap(n);
      if(old)p.Original(start,data.data(),count);else p.BenchmarkReuse(start,data.data(),count);
      if(!binding.up_to_date) {
        if(count==40)std::memcpy(binding.bytes.data(),p.storage.values.data()+BB,40*sizeof(uint32_t));
        else BenchGather(reinterpret_cast<uint8_t*>(binding.bytes.data()),p.storage,map);
        ++uploads;
      }
      checksum=checksum+p.storage.values[start];
    }
    double us=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-began).count()/iterations;
    assert(uploads==(old ? iterations : change ? iterations-1 : 0));
    printf("MICROBENCH %s %u dwords %s: %.4f us/write+fixture-pack, %u/%u uploads, checksum %u (CPU fixture only).\n",
      old ? "original" : "reuse",count,change==0 ? "equal" : change==1 ? "first-changed" : "last-changed",us,uploads,iterations,uint32_t(checksum));
  }
}
int main() {
  const std::pair<uint32_t,uint32_t> ranges[]={{FB,0},{FB,1},{FB+1,2},{FB+3,5},
    {FB+252,8},{FB+255,3},{FB+1023,2},{FB+1020,8},{FB,2048},{PB,1024},{FE,1},
    {BB,1},{BB+7,2},{BB+8,32},{BB,40},{BE,1},
    {0x4800,1},{0x4801,7},{0x4800,192},{0x48BF,1},{0x3FFF,2},{0x47FF,2},
    {0x4907,2},{0x4927,2},{0x2000,12}};
  for(auto [start,count]:ranges)for(unsigned pattern=0;pattern<6;++pattern)
    for(unsigned usage=0;usage<3;++usage)for(bool open:{true,false})
      for(bool dirty:{true,false})for(unsigned offset:{0u,1u,3u,8u})
        Check(start,count,pattern,usage,open,dirty,offset);
  std::mt19937 random(0xC057A17);
  for(unsigned i=0;i<3000;++i) {
    unsigned offset=random()%2048,count=random()%(2048-offset+1);
    Check(FB+offset,count,random()%6,random()%3,random()%2,random()%2,random()%9);
  }
  Check(0x4800,192,0,1,true,false,0,true);
#ifdef TEST_CONTROL_BATCHING
  for(bool debug:{false,true})for(bool logger_present:{false,true})
    for(auto [start,count]:{std::pair{0x2000u,1u},std::pair{0x3000u,64u},
        std::pair{0x2000u,8192u},std::pair{0x3fffu,2u},std::pair{0x1fffu,2u}})
      Check(start,count,1,1,true,false,1,false,debug,logger_present);
#endif
  Lifecycle();
  printf("PASS %u cases, %u retained bindings; exact bits, bank splits, usage masks, dirty/frame state, scalar/fetch callbacks and packed bytes.\n",cases,reused);
  Benchmark();
}
'''


if __name__ == '__main__':
    main()

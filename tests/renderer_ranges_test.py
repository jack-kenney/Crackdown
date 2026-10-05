"""Compare actual original/patched SharedMemory request methods without a GPU.

Portable C++20 fixture replaces residency, locks and upload boundaries with
observable state. Page selection and validation compile verbatim from the SDK.
Timing is informational; warmed allocation counts are asserted exactly.
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
    relative = 'src/graphics/shared_memory.cpp'
    patched = (source / relative).read_text(encoding='utf-8')
    original = subprocess.check_output(['git', 'show', 'HEAD:' + relative], cwd=source, text=True)
    if 'std::span<const std::pair<uint32_t, uint32_t>> requested_ranges' not in patched:
        parser.error('--source must contain the single-range allocation patch')
    actual_ranges = extract(patched, 'bool SharedMemory::RequestRanges(')
    original_ranges = extract(original, 'bool SharedMemory::RequestRanges(')
    # Beyond choosing the input span, coherency/upload code must stay identical.
    marker = '  upload_ranges_.clear();'
    if actual_ranges.split(marker, 1)[1].replace('requested_ranges', 'merged_ranges') != original_ranges.split(marker, 1)[1]:
        raise AssertionError('Patch unexpectedly changes locked page selection or upload flow')
    methods = actual_ranges
    methods += extract(patched, 'bool SharedMemory::RequestRange(')
    methods += original_ranges.replace(
        'SharedMemory::RequestRanges(', 'SharedMemory::OriginalRanges(', 1)
    methods += extract(original, 'bool SharedMemory::RequestRange(').replace(
        'SharedMemory::RequestRange(', 'SharedMemory::OriginalRange(', 1).replace(
        'return RequestRanges(', 'return OriginalRanges(', 1)
    program = PREFIX + methods + SUFFIX
    temporary = tempfile.TemporaryDirectory(prefix='renderer-ranges-') if args.output is None else None
    output = Path(temporary.name) if temporary else args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cpp = output / 'ranges_fixture.cpp'
    exe = output / ('ranges_fixture.exe' if os.name == 'nt' else 'ranges_fixture')
    cpp.write_text(program, encoding='utf-8')
    math = source / ('src/core/math_msvc.cpp' if os.name == 'nt' else 'src/core/math_gcc.cpp')
    name = Path(compiler).name.lower()
    if name in ('cl', 'cl.exe', 'clang-cl', 'clang-cl.exe'):
        command = [compiler, '/nologo', '/std:c++20', '/O2', '/EHsc',
                   '/I' + str(sdk / 'include'), str(cpp), str(math), '/Fe:' + str(exe)]
    else:
        command = [compiler, '-std=c++20', '-O2', '-I', str(sdk / 'include'),
                   str(cpp), str(math), '-o', str(exe)]
    subprocess.run(command, check=True, cwd=output)
    subprocess.run([str(exe)], check=True, timeout=60)
    if temporary:
        temporary.cleanup()


PREFIX = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <random>
#include <span>
#include <utility>
#include <vector>
#include <rex/bit.h>
#ifdef _MSC_VER
#define NOINLINE __declspec(noinline)
#else
#define NOINLINE __attribute__((noinline))
#endif
static thread_local bool count_allocations = false;
static thread_local size_t allocations = 0;
NOINLINE void* operator new(size_t size) {
  if (count_allocations) ++allocations;
  if (void* p = std::malloc(size ? size : 1)) return p;
  throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, size_t) noexcept { std::free(p); }
void* operator new[](size_t size) { return ::operator new(size); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete[](void* p, size_t) noexcept { std::free(p); }
using Range = std::pair<uint32_t,uint32_t>;
struct SharedMemory;
struct FixtureLock {
  bool held = false;
  unsigned acquisitions = 0;
  struct Guard {
    FixtureLock& lock;
    explicit Guard(FixtureLock& value) : lock(value) {
      assert(!lock.held);lock.held=true;++lock.acquisitions;
    }
    ~Guard() { lock.held=false; }
    Guard(const Guard&) = delete;
  };
  Guard Acquire() { return Guard(*this); }
};
struct SharedMemory {
  static constexpr uint32_t kBufferSize = 1u << 29;
  uint32_t page_size_log2_ = 12;
  std::vector<uint64_t> system_page_flags_valid_;
  std::vector<uint64_t> system_page_flags_valid_and_gpu_written_;
  std::vector<Range> upload_ranges_, ensured, uploaded;
  FixtureLock global_critical_region_;
  unsigned ensure_calls=0,upload_calls=0,profile_calls=0;
  std::array<uint32_t,3> counters = {0xBEEF,0xBEEF,0xBEEF};
  bool profile_active=false,record=true,upload_failure=false;
  int allocation_failure_at=-1;
  unsigned pages_before_failure=0;
  Range* mutate_input=nullptr;
  bool invalidate_during_upload=false;
  explicit SharedMemory(uint32_t page_log2=12) : page_size_log2_(page_log2) {
    auto blocks=((kBufferSize>>page_log2)+63)/64;
    system_page_flags_valid_.assign(blocks,UINT64_MAX);
    system_page_flags_valid_and_gpu_written_.assign(blocks,UINT64_MAX);
    upload_ranges_.reserve(4096);ensured.reserve(4096);uploaded.reserve(4096);
  }
  NOINLINE bool RequestRanges(const Range*,size_t);
  NOINLINE bool RequestRange(uint32_t,uint32_t);
  NOINLINE bool OriginalRanges(const Range*,size_t);
  NOINLINE bool OriginalRange(uint32_t,uint32_t);
  void SetCounter(const char* name,uint32_t value) {
    assert(profile_active && !global_critical_region_.held);
    unsigned i = std::strstr(name,"merged_count") ? 1 : std::strstr(name,"upload_count") ? 2 : 0;
    counters[i]=value;
  }
  bool EnsureHostGpuMemoryAllocated(uint32_t start,uint32_t length) {
    assert(profile_active && !global_critical_region_.held);
    if(record)ensured.emplace_back(start,length);
    if(mutate_input)*mutate_input={UINT32_MAX,UINT32_MAX};
    return int(ensure_calls++)!=allocation_failure_at;
  }
  bool UploadRanges(const std::vector<Range>& ranges) {
    assert(profile_active && !global_critical_region_.held);
    ++upload_calls;
    if(record)uploaded=ranges;
    // Model the backend boundary: validity is marked before a CPU copy, and
    // a subsequent CPU invalidation remains visible to the next request.
    unsigned written=0;
    for(const auto& range:ranges) for(uint32_t p=range.first;p<range.first+range.second;++p) {
      if(upload_failure && written>=pages_before_failure)return false;
      auto lock=global_critical_region_.Acquire();
      system_page_flags_valid_[p>>6]|=uint64_t(1)<<(p&63);
      system_page_flags_valid_and_gpu_written_[p>>6]&=~(uint64_t(1)<<(p&63));
      if(invalidate_during_upload)system_page_flags_valid_[p>>6]&=~(uint64_t(1)<<(p&63));
      ++written;
    }
    return !upload_failure;
  }
};
struct ProfileScope {
  SharedMemory& memory;
  explicit ProfileScope(SharedMemory& value):memory(value) {
    assert(!memory.profile_active);memory.profile_active=true;++memory.profile_calls;
  }
  ~ProfileScope() { memory.profile_active=false; }
};
#define SCOPE_profile_cpu_f(...) ProfileScope fixture_profile(*this)
#define COUNT_profile_set(name,value) SetCounter(name,value)
'''

SUFFIX = r'''
static unsigned cases=0;
static void Compare(const SharedMemory& a,const SharedMemory& b) {
  assert(a.system_page_flags_valid_==b.system_page_flags_valid_);
  assert(a.system_page_flags_valid_and_gpu_written_==b.system_page_flags_valid_and_gpu_written_);
  assert(a.upload_ranges_==b.upload_ranges_ && a.ensured==b.ensured && a.uploaded==b.uploaded);
  assert(a.ensure_calls==b.ensure_calls && a.upload_calls==b.upload_calls);
  assert(a.profile_calls==b.profile_calls && a.counters==b.counters);
  assert(a.global_critical_region_.acquisitions==b.global_critical_region_.acquisitions);
  assert(!a.global_critical_region_.held && !b.global_critical_region_.held);
  assert(!a.profile_active && !b.profile_active);
}
static void Dirty(SharedMemory& memory,unsigned pattern) {
  for(size_t i=0;i<memory.system_page_flags_valid_.size();++i) {
    uint64_t v=pattern==0 ? 0 : pattern==1 ? UINT64_MAX : pattern==2 ? 0xAAAAAAAAAAAAAAAAull :
      (uint64_t(0x9E3779B9u*(i+1))<<32)|(0x7F4A7C15u*(i+31));
    memory.system_page_flags_valid_[i]=v;
    memory.system_page_flags_valid_and_gpu_written_[i]=v;
  }
}
static void Check(std::vector<Range> ranges,bool expected,unsigned pattern=0,
                  int fail_allocate=-1,bool fail_upload=false,unsigned partial=0,uint32_t page_log2=12) {
  SharedMemory a(page_log2),b(page_log2);Dirty(a,pattern);Dirty(b,pattern);
  for(auto* p:{&a,&b}) {
    p->allocation_failure_at=fail_allocate;p->upload_failure=fail_upload;p->pages_before_failure=partial;
    p->upload_ranges_.emplace_back(99,3); // Early returns must retain scratch.
  }
  auto before=ranges;
  bool actual=a.RequestRanges(ranges.data(),ranges.size());
  bool original=b.OriginalRanges(ranges.data(),ranges.size());
  assert(actual==original && actual==expected && ranges==before);Compare(a,b);
  ++cases;
  if(!fail_upload && fail_allocate<0) {
    actual=a.RequestRanges(ranges.data(),ranges.size());
    original=b.OriginalRanges(ranges.data(),ranges.size());
    assert(actual==original && actual==expected);Compare(a,b);++cases;
  }
}
static void NullAndEmpty() {
  SharedMemory a,b;
  for(auto count:{size_t(0),size_t(1),size_t(300)}) {
    assert(a.RequestRanges(nullptr,count)==b.OriginalRanges(nullptr,count));Compare(a,b);++cases;
  }
  Range bad={UINT32_MAX,UINT32_MAX};
  assert(a.RequestRanges(&bad,0) && b.OriginalRanges(&bad,0));Compare(a,b);++cases;
}
static void AliasingAndInvalidation() {
  SharedMemory a,b;Dirty(a,0);Dirty(b,0);
  a.upload_ranges_={{4097,8191}};b.upload_ranges_=a.upload_ranges_;
  assert(a.RequestRanges(a.upload_ranges_.data(),1));
  assert(b.OriginalRanges(b.upload_ranges_.data(),1));Compare(a,b);++cases;
  // Residency callbacks may alter caller-owned input: it must be snapshotted.
  SharedMemory c,d;Dirty(c,0);Dirty(d,0);
  Range x={65535,2},y=x;c.mutate_input=&x;d.mutate_input=&y;
  assert(c.RequestRanges(&x,1) && d.OriginalRanges(&y,1));Compare(c,d);++cases;
  SharedMemory e,f;Dirty(e,0);Dirty(f,0);
  e.invalidate_during_upload=f.invalidate_during_upload=true;
  for(unsigned iteration=0;iteration<2;++iteration) {
    assert(e.RequestRange(1,8192) && f.OriginalRange(1,8192));Compare(e,f);++cases;
    assert(e.upload_calls==iteration+1);
  }
  // Ordinary repeated upload then CPU invalidation, retaining GPU provenance
  // on untouched pages and requesting only the newly dirtied page.
  SharedMemory g,h;Dirty(g,0);Dirty(h,0);
  assert(g.RequestRange(0,4096) && h.OriginalRange(0,4096));Compare(g,h);
  assert(g.RequestRange(0,4096) && h.OriginalRange(0,4096));Compare(g,h);
  assert(g.upload_calls==1 && g.upload_ranges_.empty());
  g.system_page_flags_valid_[0]&=~uint64_t(1);h.system_page_flags_valid_[0]&=~uint64_t(1);
  assert(g.RequestRange(0,4096) && h.OriginalRange(0,4096));Compare(g,h);++cases;
}
static void Benchmarks() {
  constexpr unsigned iterations=500000;
  for(uint32_t length:{32u,4096u,16384u})for(bool original:{true,false}) {
    SharedMemory memory;memory.record=false;
    for(unsigned i=0;i<1000;++i)assert(original ? memory.OriginalRange(8193,length) : memory.RequestRange(8193,length));
    allocations=0;count_allocations=true;
    unsigned checksum=0;
    auto start=std::chrono::steady_clock::now();
    for(unsigned i=0;i<iterations;++i)
      checksum+=original ? memory.OriginalRange(8193,length) : memory.RequestRange(8193,length);
    double elapsed=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-start).count();
    count_allocations=false;
    assert(checksum==iterations && allocations==(original ? iterations : 0));
    printf("MICROBENCH %s %u bytes: %.4f us/request, %zu allocations/%u requests (CPU fixture only).\n",
        original ? "original" : "stack",length,elapsed/iterations,allocations,iterations);
  }
}
int main() {
  NullAndEmpty();AliasingAndInvalidation();
  constexpr uint32_t B=SharedMemory::kBufferSize;
  const std::vector<Range> edge={{0,0},{UINT32_MAX,0},{B,0},{B+1,0},{0,1},{1,1},{4095,2},
    {4096,4096},{63*4096+4095,2},{64*4096-1,4097},{B-1,1},{0,B},{B-4096,4096},
    {B,1},{B+1,1},{UINT32_MAX,1},{B-1,2},{1,UINT32_MAX},{1,B}};
  for(uint32_t page_log2:{12u,14u,16u})for(unsigned pattern=0;pattern<4;++pattern)for(auto range:edge) {
    bool valid=!range.second || (range.first<=B && range.second<=B-range.first);
    Check({range},valid,pattern,-1,false,0,page_log2);
  }
  for(auto ranges:{std::vector<Range>{{60000,500},{0,100},{80,40},{100,20},{500,0}},
      {{0,8},{16,8}},{{4096,4096},{0,4096},{8192,4096}},{{0,8192},{4096,8192}},
      {{UINT32_MAX,0},{B,0}},{{0,4096},{B,1}},{{B-1,1},{1,4095},{8192,8192}}})
    for(unsigned pattern=0;pattern<4;++pattern)Check(ranges,
        std::all_of(ranges.begin(),ranges.end(),[](auto r){return !r.second || (r.first<=B && r.second<=B-r.first);}),pattern);
  Check({{0,4096}},false,0,0);Check({{0,4096}},false,1,0);
  Check({{0,4096},{8192,4096}},false,0,1);
  Check({{1,8192}},false,0,-1,true,0);Check({{1,8192}},false,0,-1,true,1);
  std::mt19937 random(0xC0DEC0DE);
  for(unsigned n=0;n<4000;++n) {
    std::vector<Range> ranges;
    auto count=n%5+1;
    for(unsigned i=0;i<count;++i) {
      uint32_t start=random()%(128*4096),length=random()%(12*4096);
      ranges.emplace_back(start,length);
    }
    Check(ranges,true,n%4);
  }
  printf("PASS: %u old/new cases; bounds, page/block alignment, lock/upload behavior, aliasing, failure and CPU invalidation.\n",cases);
  Benchmarks();
}
'''


if __name__ == '__main__':
    main()

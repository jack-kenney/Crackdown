"""Execute the actual SDK GPU timing helper with deterministic D3D boundaries.

This fixture validates query initialization, fence retirement, sampling, scope
lifetimes and CSV metadata. It does not measure hardware timing or validate the
renderer command insertion points; those require the separate live D3D12 run.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    args = parser.parse_args()
    header = (args.source / "include/rex/graphics/d3d12/gpu_timing.h").read_text()
    source = (args.source / "src/graphics/d3d12/gpu_timing.cpp").read_text()
    header = re.sub(r"^#(?:include|pragma).*\n", "", header, flags=re.M)
    source = re.sub(r"^#include.*\n", "", source, flags=re.M)
    source = re.sub(r"REXCVAR_DEFINE_.*?\.lifecycle\([^;]+;", "", source, flags=re.S)
    program = r'''
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#ifdef _WIN32
#include <share.h>
#endif
#include <unordered_set>
#include <vector>
static unsigned checks;
static void Check(bool value,const char* why) {
  ++checks; if(!value) { std::fprintf(stderr,"FAIL: %s\n",why);std::exit(1); }
}
using HRESULT=int;
#define FAILED(x) ((x)<0)
#define IID_PPV_ARGS(x) (x)
#define REXCVAR_GET(x) (x)
#define REXGPU_WARN(...) ((void)0)
#define REXGPU_INFO(...) ((void)0)
static std::string d3d12_gpu_timing_path;
static int d3d12_gpu_timing_interval=1;
static bool d3d12_gpu_timing_details=true;
constexpr int D3D12_QUERY_TYPE_TIMESTAMP=1,D3D12_QUERY_HEAP_TYPE_TIMESTAMP=2,
 D3D12_HEAP_TYPE_READBACK=3,D3D12_RESOURCE_DIMENSION_BUFFER=4,
 D3D12_TEXTURE_LAYOUT_ROW_MAJOR=5,D3D12_HEAP_FLAG_NONE=0,D3D12_RESOURCE_STATE_COPY_DEST=6;
struct D3D12_RANGE {size_t Begin,End;};
struct D3D12_QUERY_HEAP_DESC {int Type=0;unsigned Count=0;};
struct D3D12_HEAP_PROPERTIES {int Type=0;};
struct D3D12_RESOURCE_DESC {
 int Dimension=0,Layout=0;uint64_t Width=0;unsigned Height=0,DepthOrArraySize=0,MipLevels=0;
 struct {unsigned Count=0;} SampleDesc;
};
struct ID3D12QueryHeap {std::vector<uint64_t> ticks;std::vector<bool> initialized;};
struct ID3D12Resource {
 std::vector<uint64_t> data;
 HRESULT Map(unsigned,const D3D12_RANGE*,void** p) {*p=data.data();return 0;}
 void Unmap(unsigned,const D3D12_RANGE*) {}
};
struct ID3D12Fence {uint64_t completed=0;uint64_t GetCompletedValue(){return completed;}};
struct ID3D12CommandQueue {
 bool fail=false;HRESULT GetTimestampFrequency(uint64_t* p){*p=1000000;return fail?-1:0;}
};
struct ID3D12Device {
 unsigned allocations=0;bool fail=false;
 HRESULT CreateQueryHeap(const D3D12_QUERY_HEAP_DESC* desc,ID3D12QueryHeap** p) {
  ++allocations;if(fail)return -1;*p=new ID3D12QueryHeap;
  (*p)->ticks.resize(desc->Count);(*p)->initialized.resize(desc->Count);return 0;
 }
 HRESULT CreateCommittedResource(const D3D12_HEAP_PROPERTIES*,int,
    const D3D12_RESOURCE_DESC* desc,int,void*,ID3D12Resource** p) {
  ++allocations;*p=new ID3D12Resource;(*p)->data.resize(desc->Width/8);return 0;
 }
};
namespace Microsoft::WRL {
template<class T> class ComPtr {
 T* p_=nullptr;
 public:~ComPtr(){delete p_;}T* Get(){return p_;}T* operator->(){return p_;}
 T** operator&(){return &p_;}
};
}
namespace rex::graphics::d3d12 {
class DeferredCommandList {
 public:
 struct Query {ID3D12QueryHeap* heap;unsigned index;};
 struct Resolve {ID3D12QueryHeap* heap;unsigned first,count;ID3D12Resource* resource;uint64_t offset;};
 std::vector<Query> queries;std::vector<Resolve> resolves;
 void* owner=nullptr;void(*callback)(void*)=nullptr;
 void SetGpuWorkCallback(void* o,void(*c)(void*)){owner=o;callback=c;}
 void Work(){if(callback)callback(owner);}
 void D3DEndQuery(ID3D12QueryHeap* h,int,unsigned i){queries.push_back({h,i});}
 void D3DResolveQueryData(ID3D12QueryHeap* h,int,unsigned first,unsigned count,
   ID3D12Resource* r,uint64_t offset){resolves.push_back({h,first,count,r,offset});}
 void Execute() {
  static uint64_t tick=100;
  std::unordered_set<unsigned> initialized_here;
  for(auto& q:queries) {
   Check(initialized_here.insert(q.index).second,"Each query initialized once per submission");
   q.heap->ticks[q.index]=tick+=10;q.heap->initialized[q.index]=true;
  }
  for(auto& r:resolves) {
   Check(r.offset%8==0,"Readback alignment");
   for(unsigned i=0;i<r.count;++i) {
    Check(initialized_here.contains(r.first+i),"Resolve only queries ended in this submission");
    r.resource->data[r.offset/8+i]=r.heap->ticks[r.first+i];
   }
  }
 }
};
}
HELPER_HEADER
HELPER_SOURCE
using namespace rex::graphics::d3d12;
using Rows=std::vector<std::vector<std::string>>;
static Rows ReadRows(const char* path) {
 Rows rows;std::ifstream file(path);std::string line;
 while(std::getline(file,line)) {
  std::vector<std::string> row;std::istringstream stream(line);std::string part;
  while(std::getline(stream,part,','))row.push_back(part);
  rows.push_back(std::move(row));
 }
 return rows;
}
static const std::string& Field(const Rows& rows,size_t row,const char* name) {
 auto found=std::find(rows[0].begin(),rows[0].end(),name);
 Check(found!=rows[0].end(),"Expected CSV field exists");
 Check(rows[row].size()==rows[0].size(),"CSV field count matches header");
 return rows[row][size_t(found-rows[0].begin())];
}
int main() {
 ID3D12Device device;ID3D12CommandQueue queue;ID3D12Fence fence;
 {
  D3D12GpuTiming timing;DeferredCommandList list;
  timing.Initialize(&device,&queue);timing.BeginSubmission(list,0,1);
  {D3D12GpuTiming::Scope scope(timing,GpuTimingCategory::kTextures);list.Work();}
  timing.EndSubmission(true);timing.PollCompleted(&fence);timing.Shutdown();
  Check(device.allocations==0&&list.queries.empty(),"Disabled allocates and records nothing");
 }
 d3d12_gpu_timing_path="failure.csv";
 {
  D3D12GpuTiming timing;DeferredCommandList list;device.fail=true;
  timing.Initialize(&device,&queue);timing.BeginSubmission(list,0,1);timing.EndSubmission(true);
  Check(list.queries.empty(),"Failed initialization preserves rendering");device.fail=false;
 }
 d3d12_gpu_timing_path="basic.csv";
 {
  D3D12GpuTiming timing;timing.Initialize(&device,&queue);DeferredCommandList first,second;
  timing.BeginSubmission(first,4,1);
  {D3D12GpuTiming::Scope empty(timing,GpuTimingCategory::kRenderTargets);}
  Check(first.queries.size()==1,"Empty CPU scope consumes no GPU queries");
  auto late=std::make_unique<D3D12GpuTiming::Scope>(timing,GpuTimingCategory::kResolve);
  first.Work();
  {D3D12GpuTiming::Scope nested(timing,GpuTimingCategory::kReadback);first.Work();}
  auto lazy_late=std::make_unique<D3D12GpuTiming::Scope>(timing,GpuTimingCategory::kTextures);
  timing.CountDraw();timing.CountResolve();timing.CountTextureLoad();timing.CountUploadBytes(4096);
  timing.EndSubmission(false);auto count=first.queries.size();timing.EndSubmission(false);
  Check(first.queries.size()==count&&first.resolves.size()==1,"EndSubmission is idempotent");
  Check(first.callback==nullptr,"Forced closure removes work callback");
  Check(first.resolves[0].count==6,"Initialized contiguous total and nested pairs resolved");
  first.Execute();timing.PollCompleted(&fence);
  Check(ReadRows("basic.csv").size()==1,"Unretired fence exposes no rows");
  timing.BeginSubmission(second,4,2);
  auto current=std::make_unique<D3D12GpuTiming::Scope>(timing,GpuTimingCategory::kGamma);
  second.Work();auto newcount=second.queries.size();late.reset();
  Check(second.queries.size()==newcount,"Late RAII cannot close a new submission scope");
  current.reset();timing.EndSubmission(true);second.Execute();fence.completed=2;
  timing.PollCompleted(&fence);timing.Shutdown();
  auto rows=ReadRows("basic.csv");Check(rows.size()==3,"Both completed submissions retired");
  Check(Field(rows,1,"first_in_frame")=="1"&&Field(rows,2,"first_in_frame")=="0",
    "One first marker across multiple submissions");
  Check(Field(rows,1,"closes_frame")=="0"&&Field(rows,2,"closes_frame")=="1","Frame closure metadata");
  Check(Field(rows,1,"forced_scope_ends")=="2","Truncated initialized and lazy detail scopes reported");
  Check(Field(rows,1,"recorded_guest_draws")=="1"&&Field(rows,1,"upload_bytes")=="4096","Work counters");
  Check(Field(rows,1,"resolve_scopes")=="1"&&Field(rows,1,"readback_scopes")=="1","Nested inclusive categories");
  Check(Field(rows,2,"gamma_scopes")=="1"&&Field(rows,2,"valid")=="1","Late scope does not corrupt queries");
 }
 d3d12_gpu_timing_path="ring.csv";fence.completed=0;
 {
  D3D12GpuTiming timing;timing.Initialize(&device,&queue);
  std::array<DeferredCommandList,32> lists;
  for(unsigned i=0;i<32;++i) {
   timing.BeginSubmission(lists[i],10,i+1);timing.EndSubmission(false);lists[i].Execute();
  }
  DeferredCommandList skipped;timing.BeginSubmission(skipped,11,33);timing.EndSubmission(false);
  Check(skipped.queries.empty(),"All 32 in-flight slots skip without overwrite/wait");
  timing.PollCompleted(&fence);DeferredCommandList still_busy;
  timing.BeginSubmission(still_busy,11,34);timing.EndSubmission(false);
  Check(still_busy.queries.empty(),"Uncompleted slot remains unavailable after poll");
  fence.completed=1;timing.PollCompleted(&fence);
  DeferredCommandList recovered;timing.BeginSubmission(recovered,11,35);timing.EndSubmission(true);
  Check(recovered.queries.size()==2,"CPU-consumed completed slot can be reused");recovered.Execute();
  fence.completed=35;timing.PollCompleted(&fence);
  DeferredCommandList clean;timing.BeginSubmission(clean,12,36);timing.EndSubmission(true);clean.Execute();
  fence.completed=36;timing.PollCompleted(&fence);timing.Shutdown();
  auto rows=ReadRows("ring.csv");Check(rows.size()==35,"Skipped submissions do not emit fabricated rows");
  size_t recovery=0;for(size_t i=1;i<rows.size();++i)if(Field(rows,i,"submission")=="35")recovery=i;
  Check(recovery!=0&&Field(rows,recovery,"first_in_frame")=="0","Skipped first cannot masquerade as first");
  Check(Field(rows,recovery,"frame_skipped_submissions")=="2"&&Field(rows,recovery,"skipped_submissions")=="2",
    "Per-frame and cumulative skipped submissions snapshots");
  size_t cleanrow=0;for(size_t i=1;i<rows.size();++i)if(Field(rows,i,"submission")=="36")cleanrow=i;
  Check(cleanrow!=0&&Field(rows,cleanrow,"first_in_frame")=="1"&&
    Field(rows,cleanrow,"frame_skipped_submissions")=="0"&&Field(rows,cleanrow,"skipped_submissions")=="2",
    "Prior frame skips never contaminate next complete frame");
 }
 d3d12_gpu_timing_path="budget.csv";fence.completed=0;
 {
  D3D12GpuTiming timing;timing.Initialize(&device,&queue);DeferredCommandList list;
  timing.BeginSubmission(list,12,1);
  for(unsigned i=0;i<520;++i){D3D12GpuTiming::Scope scope(timing,GpuTimingCategory::kTextures);list.Work();list.Work();}
  timing.EndSubmission(true);Check(list.resolves[0].count==1024,"Query budget never exceeded");list.Execute();
  fence.completed=1;timing.PollCompleted(&fence);timing.Shutdown();auto rows=ReadRows("budget.csv");
  Check(Field(rows,1,"query_pairs")=="512"&&Field(rows,1,"dropped_scopes")=="9","Each budget-dropped scope counted once");
 }
 d3d12_gpu_timing_path="interval.csv";d3d12_gpu_timing_interval=3;d3d12_gpu_timing_details=false;
 {
  D3D12GpuTiming timing;timing.Initialize(&device,&queue);DeferredCommandList excluded,included;
  timing.BeginSubmission(excluded,4,1);timing.EndSubmission(true);
  Check(excluded.queries.empty(),"Frame interval selection excludes unsampled frames");
  timing.BeginSubmission(included,6,2);
  {D3D12GpuTiming::Scope scope(timing,GpuTimingCategory::kTextures);included.Work();}
  timing.EndSubmission(true);Check(included.queries.size()==2&&included.callback==nullptr,"Whole-only avoids detail callback/query work");
  included.Execute();fence.completed=2;timing.PollCompleted(&fence);timing.Shutdown();
  auto rows=ReadRows("interval.csv");Check(Field(rows,1,"details")=="0"&&Field(rows,1,"texture_scopes")=="0","Whole-only metadata");
 }
 d3d12_gpu_timing_path="lost.csv";d3d12_gpu_timing_interval=1;
 {
  D3D12GpuTiming timing;timing.Initialize(&device,&queue);DeferredCommandList first,next;
  timing.BeginSubmission(first,1,1);timing.EndSubmission(true);first.Execute();
  fence.completed=UINT64_MAX;timing.PollCompleted(&fence);timing.BeginSubmission(next,2,2);
  Check(next.queries.empty(),"Device-loss fence disables new instrumentation");timing.Shutdown();
  Check(ReadRows("lost.csv").size()==1,"Device loss never retires potentially invalid readback data");
 }
 std::printf("PASS: actual GPU timing helper lifecycle (%u checks)\n",checks);
}
'''
    program = program.replace("HELPER_HEADER", header).replace("HELPER_SOURCE", source)
    with tempfile.TemporaryDirectory(prefix="crackdown-gpu-timing-") as directory:
        folder = Path(directory)
        fixture, exe = folder / "gpu_timing.cpp", folder / "gpu_timing.exe"
        fixture.write_text(program, encoding="utf-8")
        subprocess.run([
            args.cxx, "-std=c++23", "-O2", "-DNDEBUG", "-D_DLL", "-D_MT",
            "-Xclang", "--dependent-lib=msvcrt", str(fixture), "-o", str(exe), "-fuse-ld=lld-link",
        ], check=True)
        subprocess.run([str(exe)], cwd=folder, check=True, timeout=15)


if __name__ == "__main__":
    main()

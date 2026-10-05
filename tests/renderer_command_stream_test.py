"""Differential-test actual SDK deferred command writers and replay.

Executes git HEAD and candidate methods against the same D3D recording facade.
Poisons retained storage to catch dependence on old zero-initialization, and
checks growth, reset, shorter submissions, nullable arguments and alignment.
No GPU device is created; live rendering validation remains separate.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', required=True, type=Path)
parser.add_argument('--sdk', required=True, type=Path)
parser.add_argument('--cxx', required=True)
args = parser.parse_args()
base = args.source.resolve()
names = ['include/rex/graphics/d3d12/deferred_command_list.h',
         'src/graphics/d3d12/deferred_command_list.cpp']
original = [subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=base,
                                    text=True) for name in names]
patched = [(base / name).read_text() for name in names]


def actual_methods(texts, namespace, callbacks=False):
    header, source = [re.sub(r'^#(?:include|pragma).*\n', '', text, flags=re.M)
                      for text in texts]
    if callbacks:
        # Apply exactly the callback changes used by the separate GPU profiler.
        header = header.replace('  void Reset();', '''  void Reset();
  void SetGpuWorkCallback(void* context, void(*callback)(void*)) {
    gpu_work_context_ = context; gpu_work_callback_ = callback;
  }''')
        header = header.replace('  const D3D12CommandProcessor& command_processor_;', '''
  void* gpu_work_context_ = nullptr;
  void(*gpu_work_callback_)(void*) = nullptr;
  const D3D12CommandProcessor& command_processor_;''')
        source = source.replace('void DeferredCommandList::Reset() {', '''void DeferredCommandList::Reset() {
  gpu_work_context_ = nullptr; gpu_work_callback_ = nullptr;''')
        source = source.replace('void* DeferredCommandList::WriteCommand(Command command, size_t arguments_size_bytes) {', '''void* DeferredCommandList::WriteCommand(Command command, size_t arguments_size_bytes) {
  if (gpu_work_callback_ && command <= Command::kD3DDrawInstanced)
    gpu_work_callback_(gpu_work_context_);''')
    text = header + '\n' + source
    text = text.replace('namespace rex::graphics::d3d12', 'namespace ' + namespace)
    text = text.replace('ID3D12GraphicsCommandList1', 'FixtureCommandList')
    text = text.replace('ID3D12GraphicsCommandList', 'FixtureCommandList')
    preamble = 'namespace ' + namespace + ' { class D3D12CommandProcessor { public: ID3D12PipelineState* GetD3D12PipelineByHandle(void* handle) const { return static_cast<ID3D12PipelineState*>(handle); } }; }\n'
    return preamble + text


prefix = r'''
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#define XE_GPU_FINE_GRAINED_DRAW_SCOPES 0
#include <rex/ui/d3d12/d3d12_api.h>
#include <rex/literals.h>
#include <rex/math.h>
#include <algorithm>
#include <array>
#include <bit>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <type_traits>
#include <vector>
static unsigned checks;
static void Check(bool condition,const char* why) {
 ++checks;if(!condition){std::fprintf(stderr,"FAIL: %s\n",why);std::exit(1);}
}
#undef assert_not_null
#undef assert_unhandled_case
#define assert_not_null(p) Check((p)!=nullptr,"Non-null allocated command storage")
#define assert_unhandled_case(p) Check(false,"Known opcode")
class FixtureCommandList {
 public:
 std::vector<uint64_t> output;
 template<class T> void Add(T value) {
  if constexpr(std::is_pointer_v<T>)output.push_back(uintptr_t(value));
  else if constexpr(std::is_same_v<T,float>)output.push_back(std::bit_cast<uint32_t>(value));
  else output.push_back(uint64_t(value));
 }
 template<class... T> void Call(unsigned opcode,T... value){Add(opcode);(Add(value),...);}
 template<class T> void Blob(const T* data,size_t count){
  Check(data!=nullptr||count==0,"Non-null input when count nonzero");
  const auto* bytes=reinterpret_cast<const unsigned char*>(data);
  for(size_t i=0;i<count*sizeof(T);++i)Add(bytes[i]);
 }
 void ClearDepthStencilView(D3D12_CPU_DESCRIPTOR_HANDLE view,D3D12_CLEAR_FLAGS flags,
  FLOAT depth,UINT8 stencil,UINT count,const D3D12_RECT* rects){Call(1,view.ptr,flags,depth,stencil,count);Blob(rects,count);}
 void ClearRenderTargetView(D3D12_CPU_DESCRIPTOR_HANDLE view,const FLOAT* color,
  UINT count,const D3D12_RECT* rects){Call(2,view.ptr,count);Blob(color,4);Blob(rects,count);}
 void ClearUnorderedAccessViewUint(D3D12_GPU_DESCRIPTOR_HANDLE gpu,D3D12_CPU_DESCRIPTOR_HANDLE cpu,
  ID3D12Resource* resource,const UINT* values,UINT count,const D3D12_RECT* rects){Call(3,gpu.ptr,cpu.ptr,resource,count);Blob(values,4);Blob(rects,count);}
 template<class... T>void CopyBufferRegion(T... v){Call(4,v...);}
 template<class... T>void CopyResource(T... v){Call(5,v...);}
 void CopyTextureRegion(const D3D12_TEXTURE_COPY_LOCATION* dst,UINT x,UINT y,UINT z,
  const D3D12_TEXTURE_COPY_LOCATION* src,const D3D12_BOX* box){Call(6,x,y,z,box!=nullptr);Blob(dst,1);Blob(src,1);Blob(box,box?1:0);}
 template<class... T>void Dispatch(T... v){Call(7,v...);}
 template<class... T>void DrawIndexedInstanced(T... v){Call(8,v...);}
 template<class... T>void DrawInstanced(T... v){Call(9,v...);}
 template<class... T>void BeginQuery(T... v){Call(10,v...);}
 template<class... T>void EndQuery(T... v){Call(11,v...);}
 template<class... T>void ResolveQueryData(T... v){Call(12,v...);}
 void IASetIndexBuffer(const D3D12_INDEX_BUFFER_VIEW* view){Call(13,view!=nullptr);Blob(view,view?1:0);}
 template<class... T>void IASetPrimitiveTopology(T... v){Call(14,v...);}
 void IASetVertexBuffers(UINT slot,UINT count,const D3D12_VERTEX_BUFFER_VIEW* views){Call(15,slot,count);Blob(views,count);}
 void OMSetBlendFactor(const FLOAT* value){Call(16);Blob(value,4);}
 void OMSetRenderTargets(UINT count,const D3D12_CPU_DESCRIPTOR_HANDLE* views,BOOL range,
  const D3D12_CPU_DESCRIPTOR_HANDLE* depth){Call(17,count,range,depth!=nullptr);Blob(views,count?(range?1:count):0);Blob(depth,depth?1:0);}
 template<class... T>void OMSetStencilRef(T... v){Call(18,v...);}
 void ResourceBarrier(UINT count,const D3D12_RESOURCE_BARRIER* barriers){Call(19,count);Blob(barriers,count);}
 void RSSetScissorRects(UINT count,const D3D12_RECT* rects){Call(20,count);Blob(rects,count);}
 void RSSetViewports(UINT count,const D3D12_VIEWPORT* views){Call(21,count);Blob(views,count);}
 void SetComputeRoot32BitConstants(UINT index,UINT count,const void* data,UINT offset){Call(22,index,count,offset);Blob(static_cast<const uint32_t*>(data),count);}
 void SetGraphicsRoot32BitConstants(UINT index,UINT count,const void* data,UINT offset){Call(23,index,count,offset);Blob(static_cast<const uint32_t*>(data),count);}
 template<class... T>void SetComputeRootConstantBufferView(T... v){Call(24,v...);}
 template<class... T>void SetGraphicsRootConstantBufferView(T... v){Call(25,v...);}
 void SetComputeRootDescriptorTable(UINT index,D3D12_GPU_DESCRIPTOR_HANDLE handle){Call(26,index,handle.ptr);}
 void SetGraphicsRootDescriptorTable(UINT index,D3D12_GPU_DESCRIPTOR_HANDLE handle){Call(27,index,handle.ptr);}
 template<class... T>void SetComputeRootShaderResourceView(T... v){Call(28,v...);}
 template<class... T>void SetGraphicsRootShaderResourceView(T... v){Call(29,v...);}
 template<class... T>void SetComputeRootSignature(T... v){Call(30,v...);}
 template<class... T>void SetGraphicsRootSignature(T... v){Call(31,v...);}
 template<class... T>void SetComputeRootUnorderedAccessView(T... v){Call(32,v...);}
 template<class... T>void SetGraphicsRootUnorderedAccessView(T... v){Call(33,v...);}
 void SetDescriptorHeaps(UINT count,ID3D12DescriptorHeap* const* heaps){Call(34,count);Blob(heaps,count);}
 template<class... T>void SetPipelineState(T... v){Call(35,v...);}
 void SetSamplePositions(UINT samples,UINT pixels,D3D12_SAMPLE_POSITION* positions){Call(36,samples,pixels);Blob(positions,samples*pixels);}
 void BeginEvent(UINT metadata,const void* data,UINT size){Call(37,metadata,size);Blob(static_cast<const uint8_t*>(data),size);}
 void EndEvent(){Call(38);}
 void SetMarker(UINT metadata,const void* data,UINT size){Call(39,metadata,size);Blob(static_cast<const uint8_t*>(data),size);}
};
'''

suffix = r'''
using namespace rex::literals;
template<class T> static T* Ptr(unsigned value){return reinterpret_cast<T*>(uintptr_t(value)*0x1000);}
template<class List>static void Record(List& list,unsigned seed,unsigned rounds) {
 uint32_t random=seed;
 auto Next=[&](){random=random*1664525+1013904223;return random;};
 for(unsigned n=0;n<rounds;++n) {
  unsigned v=Next(),count=(v>>8)%5;
  std::array<D3D12_RECT,5> rects{};
  for(unsigned i=0;i<5;++i)rects[i]={LONG(v+i),LONG(v+i+1),LONG(v+i+2),LONG(v+i+3)};
  std::array<FLOAT,4> floats{float(v%71),float(v%37),float(v%19),float(v%11)};
  std::array<UINT,4> integers{v,v+1,v+2,v+3};
  std::array<D3D12_CPU_DESCRIPTOR_HANDLE,8> descriptors{};
  for(unsigned i=0;i<8;++i)descriptors[i].ptr=v+i+17;
  std::array<D3D12_VERTEX_BUFFER_VIEW,5> vertex{};
  for(unsigned i=0;i<5;++i)vertex[i]={uint64_t(v)+i+5,v%71+1,v%19+4};
  std::array<D3D12_RESOURCE_BARRIER,5> barriers{};
  for(unsigned i=0;i<5;++i){barriers[i].Type=D3D12_RESOURCE_BARRIER_TYPE_UAV;barriers[i].UAV.pResource=Ptr<ID3D12Resource>(i+1);}
  std::array<D3D12_SAMPLE_POSITION,16> positions{};
  for(unsigned i=0;i<16;++i)positions[i]={INT8(i-8),INT8(8-i)};
  D3D12_TEXTURE_COPY_LOCATION texture{};texture.pResource=Ptr<ID3D12Resource>(5);texture.Type=D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;texture.SubresourceIndex=v%31;
  D3D12_BOX box{1,2,3,4,5,6};
  D3D12_INDEX_BUFFER_VIEW index{uint64_t(v)+7,v%99+8,DXGI_FORMAT_R16_UINT};
  D3D12_VIEWPORT viewport{floats[0],floats[1],floats[2],floats[3],0,1};
  list.D3DDispatch(1,2,3); // Starts without a valid pipeline on first iteration.
  list.D3DSetPipelineState(Ptr<ID3D12PipelineState>(7));
  auto allocated=list.ClearDepthStencilViewAllocatedRects(descriptors[0],D3D12_CLEAR_FLAG_STENCIL,0.75f,UINT8(v),count);
  if(count)std::memcpy(allocated,rects.data(),count*sizeof(D3D12_RECT));
  list.D3DClearDepthStencilView(descriptors[1],D3D12_CLEAR_FLAG_DEPTH,0.125f,0,count,rects.data());
  list.D3DClearRenderTargetView(descriptors[0],floats.data(),count,rects.data());
  list.D3DClearUnorderedAccessViewUint({v+5},descriptors[0],Ptr<ID3D12Resource>(3),integers.data(),count,rects.data());
  list.D3DCopyBufferRegion(Ptr<ID3D12Resource>(1),v,Ptr<ID3D12Resource>(2),v+3,v+8);
  list.D3DCopyResource(Ptr<ID3D12Resource>(1),Ptr<ID3D12Resource>(2));
  list.CopyTexture(texture,texture);
  list.D3DCopyTextureRegion(&texture,1,2,3,&texture,(v&1)?&box:nullptr);
  list.D3DDispatch(1,2,3);list.D3DDrawIndexedInstanced(v,1,2,-5,3);list.D3DDrawInstanced(v,2,4,6);
  list.D3DBeginQuery(Ptr<ID3D12QueryHeap>(4),D3D12_QUERY_TYPE_OCCLUSION,v);
  list.D3DEndQuery(Ptr<ID3D12QueryHeap>(4),D3D12_QUERY_TYPE_TIMESTAMP,v+1);
  list.D3DResolveQueryData(Ptr<ID3D12QueryHeap>(4),D3D12_QUERY_TYPE_TIMESTAMP,0,2,Ptr<ID3D12Resource>(5),16);
  list.D3DIASetIndexBuffer((v&1)?&index:nullptr);list.D3DIASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
  list.D3DIASetVertexBuffers(2,count,vertex.data());list.D3DOMSetBlendFactor(floats.data());
  list.D3DOMSetRenderTargets(count,descriptors.data(),BOOL(v&1),(v&2)?&descriptors[7]:nullptr);
  list.D3DOMSetStencilRef(v%256);list.D3DResourceBarrier(count,barriers.data());
  list.RSSetScissorRect(rects[0]);list.RSSetViewport(viewport);
  list.D3DSetComputeRoot32BitConstants(3,count?count-1:0,integers.data(),2);
  list.D3DSetGraphicsRoot32BitConstants(4,count?count-1:0,integers.data(),1);
  list.D3DSetComputeRootConstantBufferView(1,v);list.D3DSetGraphicsRootConstantBufferView(2,v+1);
  list.D3DSetComputeRootDescriptorTable(2,{v});list.D3DSetGraphicsRootDescriptorTable(3,{v+1});
  list.D3DSetComputeRootShaderResourceView(4,v);list.D3DSetGraphicsRootShaderResourceView(5,v+1);
  list.D3DSetComputeRootSignature(Ptr<ID3D12RootSignature>(2));list.D3DSetGraphicsRootSignature(Ptr<ID3D12RootSignature>(3));
  list.D3DSetComputeRootUnorderedAccessView(6,v);list.D3DSetGraphicsRootUnorderedAccessView(7,v+1);
  list.SetDescriptorHeaps((v&1)?Ptr<ID3D12DescriptorHeap>(2):nullptr,(v&2)?Ptr<ID3D12DescriptorHeap>(3):nullptr);
  list.SetPipelineStateHandle((v&1)?Ptr<void>(4):nullptr);list.D3DDispatch(3,2,1);list.D3DDrawInstanced(9,8,7,6);
  list.D3DSetSamplePositions((v&1)?4:0,(v&1)?4:0,positions.data());
  list.BeginDebugMarker((v&1)?"variable-length debug label":"");list.InsertDebugMarker("marker");list.EndDebugMarker();
  // Caller storage goes away and mutates: deferred writers must capture values.
  floats.fill(-1);integers.fill(0);rects.fill({});
 }
}
template<class List>static void CheckAlignment(List& list,size_t used) {
 const auto* stream=list.command_stream_.data();size_t remaining=used;
 while(remaining) {
  auto* header=reinterpret_cast<const typename List::CommandHeader*>(stream);
  Check(uintptr_t(stream)%alignof(uintmax_t)==0,"Command header alignment");
  const auto size=List::kCommandHeaderSizeElements+header->arguments_size_elements;
  Check(size<=remaining,"Recorded command fits used prefix");
  stream+=size;remaining-=size;
 }
}
template<class Original,class Candidate,class CP1,class CP2>
static void Differential(CP1& cp1,CP2& cp2) {
 Original before(cp1,8);Candidate after(cp2,8);
 size_t high_water=0;
 for(unsigned test=0;test<240;++test) {
  before.Reset();after.Reset();
  Check(after.command_stream_used_==0&&after.command_stream_.size()==high_water,"Reset only discards used prefix");
  std::fill(after.command_stream_.begin(),after.command_stream_.end(),UINT64_C(0xdeadbeefbad0cafe));
  const unsigned rounds=(test%7==0)?0:(test%11==0)?37:test%9+1;
  Record(before,test*91+17,rounds);Record(after,test*91+17,rounds);
  Check(after.command_stream_used_==before.command_stream_.size(),"Identical encoded command length");
  high_water=std::max(high_water,after.command_stream_used_);
  Check(after.command_stream_.size()==high_water,"Constructed storage retains exact high-water size");
  CheckAlignment(before,before.command_stream_.size());CheckAlignment(after,after.command_stream_used_);
  FixtureCommandList one,two,one1,two1;
  before.Execute(&one,(test&1)?&one1:nullptr);after.Execute(&two,(test&1)?&two1:nullptr);
  Check(one.output==two.output&&one1.output==two1.output,"Original/candidate replay arguments and order match");
  before.Reset();after.Reset();FixtureCommandList empty1,empty2;
  before.Execute(&empty1,nullptr);after.Execute(&empty2,nullptr);
  Check(empty1.output.empty()&&empty2.output.empty(),"Reset never replays old commands");
 }
}
int main() {
 baseline::D3D12CommandProcessor cp1;candidate::D3D12CommandProcessor cp2;
 Differential<baseline::DeferredCommandList,candidate::DeferredCommandList>(cp1,cp2);
 callbacks_before::D3D12CommandProcessor cp3;callbacks_after::D3D12CommandProcessor cp4;
 Differential<callbacks_before::DeferredCommandList,callbacks_after::DeferredCommandList>(cp3,cp4);
 {
  callbacks_after::DeferredCommandList list(cp4,8);
  struct Callback {callbacks_after::DeferredCommandList* list;unsigned calls=0;} context{&list};
  auto work=[](void* p){auto& c=*static_cast<Callback*>(p);++c.calls;
    c.list->D3DEndQuery(Ptr<ID3D12QueryHeap>(4),D3D12_QUERY_TYPE_TIMESTAMP,c.calls);};
  for(unsigned i=0;i<8;++i) {
   list.Reset();list.SetGpuWorkCallback(&context,work);
   list.D3DSetPipelineState(Ptr<ID3D12PipelineState>(7));
   list.D3DDrawInstanced(i+1,1,0,0);
   FixtureCommandList replay;list.Execute(&replay,nullptr);
   Check(context.calls==i+1,"GPU work callback runs once without query recursion");
   Check(replay.output.size()==11&&replay.output[2]==11&&replay.output[6]==9,"Callback query precedes work after growth/reuse");
   list.Reset();Check(list.gpu_work_callback_==nullptr,"Reset removes lazy GPU callback");
  }
 }
 {
  baseline::DeferredCommandList before(cp1);candidate::DeferredCommandList after(cp2);
  volatile uint64_t recording_sink=17;
  auto Run=[&](auto& list) {
   for(unsigned frame=0;frame<1200;++frame){list.Reset();for(unsigned draw=0;draw<4000;++draw){
    list.D3DSetGraphicsRootConstantBufferView(2,draw*256);
    list.D3DSetGraphicsRootDescriptorTable(3,{draw*32ull});
    list.D3DDrawIndexedInstanced(20,1,0,0,0);
   }
   size_t used=0;
   if constexpr(requires{list.command_stream_used_;})used=list.command_stream_used_;
   else used=list.command_stream_.size();
   const auto* observable=reinterpret_cast<const volatile uintmax_t*>(list.command_stream_.data());
   recording_sink=observable[recording_sink%used];
  }
  };
  auto t0=std::chrono::steady_clock::now();Run(before);auto t1=std::chrono::steady_clock::now();
  Run(after);auto t2=std::chrono::steady_clock::now();
  std::printf("Informational recording microbenchmark original %.2fms reused %.2fms; no FPS claim.\n",
   std::chrono::duration<double,std::milli>(t1-t0).count(),std::chrono::duration<double,std::milli>(t2-t1).count());
 }
 std::printf("PASS: actual deferred record/replay equivalence, poisoned reuse, high-water reset, alignment, nullable/count cases and GPU callbacks (%u checks)\n",checks);
}
'''

program = prefix + actual_methods(original, 'baseline') + actual_methods(patched, 'candidate')
program += actual_methods(original, 'callbacks_before', True)
program += actual_methods(patched, 'callbacks_after', True) + suffix
with tempfile.TemporaryDirectory(prefix='renderer-command-stream-') as directory:
    folder = Path(directory)
    cpp, exe = folder / 'fixture.cpp', folder / 'fixture.exe'
    cpp.write_text(program, encoding='utf-8')
    subprocess.run([
        args.cxx, '-std=c++23', '-O2', '-DNDEBUG', '-D_DLL', '-D_MT',
        '-Xclang', '--dependent-lib=msvcrt', '-fno-access-control', '-fno-char8_t',
        '-isystem', str(args.sdk / 'include'), '-isystem', str(args.sdk / 'include/dxc'),
        '-isystem', str(args.sdk / 'include/renderdoc'), str(cpp), '-o', str(exe), '-fuse-ld=lld-link',
    ], check=True)
    subprocess.run([str(exe)], cwd=folder, check=True, timeout=30)

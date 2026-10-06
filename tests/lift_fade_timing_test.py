"""Execute the generated TU0 cLift fade, preserving its transforms and clamps.

Game-derived function bodies are local generated inputs. The three transform
getter/copy/set boundaries are stubbed; the original branch and fade arithmetic
run in the fixture at every tested rate.
"""
import argparse
import csv
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generated', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--cxx', required=True)
    args = parser.parse_args()
    bodies = {}
    pattern = re.compile(r'DEFINE_REX_FUNC\(([^)]+)\) \{.*?\n\}', re.S)
    names = ('sub_82531228', 'sub_82531880')
    for path in args.generated.glob('crackdown_recomp.*.cpp'):
        for match in pattern.finditer(path.read_text(encoding='utf-8')):
            if match[1] in names:
                if match[1] in bodies:
                    raise AssertionError('Duplicate lift routine')
                bodies[match[1]] = match[0]
    if set(bodies) != set(names):
        raise AssertionError('Generate current TU0 code before running lift tests')
    fade = bodies[names[0]]
    hook = '\tCrackdownLiftFadeStep(ctx.f13, ctx.f31);\n'
    if fade.count(hook) != 1:
        raise AssertionError('Expected exactly one consumed fade increment hook')
    if not re.search(r'ctx\.f13\.f64 = double\(temp\.f32\);\s*// beq cr6,0x82531328\s*CrackdownLiftFadeStep\(ctx\.f13, ctx\.f31\);\s*if \(ctx\.cr6\.eq\)', fade):
        raise AssertionError('Fade hook must run after the load and before either branch')
    parent = bodies[names[1]]
    if not re.search(r'ctx\.f31\.f64 = ctx\.f1\.f64;.*?ctx\.lr = 0x825318A4;\s*sub_82531228\(ctx, base\)', parent, re.S):
        raise AssertionError('Caller must retain its update interval in f31')
    original = fade.replace(hook, '').replace('DEFINE_REX_FUNC(sub_82531228)', 'REX_EXTERN(original_fade)')
    actual = fade.replace('DEFINE_REX_FUNC(sub_82531228)', 'REX_EXTERN(__imp__sub_82531228)')
    program = r'''
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "crackdown_funcs.h"
#include "src/lift_fade_timing.cpp"
#include <array>
#include <cstdio>
#include <cstring>
#include <limits>
static bool native = false;
bool crackdown::NativeTimingActive() { return native; }
static unsigned checks=0, getters=0, copies=0, setters=0, originals=0;
static constexpr uint32_t object=0x10000, visual=0x20000, transform=0x28000, stack=0x3F000;
static void Check(bool valid, const char* message) {
    ++checks;
    if (!valid) { std::fprintf(stderr,"Lift fade: %s\n",message);std::exit(1); }
}
static void Near(float value,float expected) {
    if(std::abs(value-expected)>1e-5) std::fprintf(stderr,"Got %.9g expected %.9g\n",value,expected);
    Check(std::abs(value-expected)<=1e-5,"Elapsed-time fade value");
}
static float Load(uint8_t* base,uint32_t address) { return std::bit_cast<float>(REX_LOAD_U32(address)); }
static void Store(uint8_t* base,uint32_t address,float value) { REX_STORE_U32(address,std::bit_cast<uint32_t>(value)); }
static PPCContext Context(float dt=1.f/30) {
    PPCContext ctx{};ctx.fpscr.InitHost();ctx.r1.u32=stack;ctx.r3.u32=object;
    ctx.r31.u64=0xFEDCBA9876543210ull;ctx.f31.f64=double(dt);ctx.lr=0x825318A4;
    return ctx;
}
static void Getter(PPCContext& ctx,uint8_t* base,uint32_t address) {
    Check(address==0x82100000,"Original transform getter dispatch");++getters;ctx.r3.u32=transform;
    ctx.f1.f64=17.25; // legal volatile clobber: f31 must supply elapsed time.
}
#undef REX_CALL_INDIRECT_FUNC
#define REX_CALL_INDIRECT_FUNC(address) Getter(ctx,base,address)
REX_EXTERN(sub_8213C980) { ++copies;std::memcpy(base+ctx.r3.u32,base+ctx.r4.u32,64);ctx.f1.f64=23.5; }
REX_EXTERN(sub_8229AE88) { ++setters;Check(ctx.r3.u32==visual || ctx.r3.u32==visual+512 || ctx.r3.u32==visual+1024,"Original attached visual");ctx.f1.f64=42; }
ORIGINAL_FUNCTION
ACTUAL_FUNCTION
static void Seed(uint8_t* base,float alpha,bool fading_out) {
    std::memset(base+object,0,4096);std::memset(base+visual,0,4096);std::memset(base+stack-4096,0xAA,4096);
    REX_STORE_U32(object,0x820C43C8);REX_STORE_U32(0x820C43C8+336,0x82100000);
    REX_STORE_U32(object+2404,visual);REX_STORE_U32(object+2408,visual+512);REX_STORE_U32(object+2412,visual+1024);
    REX_STORE_U8(object+2328,fading_out);
    for(unsigned offset=2352;offset<=2392;offset+=4) Store(base,object+offset,float(offset)/100);
    for(unsigned offset : {124u,128u,132u,136u}) Store(base,visual+offset,alpha);
    Store(base,0x82072048,1.f/15);Store(base,0x820722CC,1);Store(base,0x820722DC,0);
    getters=copies=setters=0;
}
static float Advance(uint8_t* base,float dt) {
    auto ctx=Context(dt);auto saved31=ctx.f31.u64;
    sub_82531228(ctx,base);
    Check(ctx.r1.u32==stack && ctx.lr==0x825318A4 && ctx.r31.u64==0xFEDCBA9876543210ull,"Stack, LR and nonvolatile register preservation");
    Check(ctx.f31.u64==saved31,"Retained update interval preserved");
    auto value=Load(base,visual+136);
    for(unsigned offset : {124u,128u,132u}) Check(Load(base,visual+offset)==value,"All four fade channels agree");
    return value;
}
int main(int argc,char** argv) {
    auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,0x86000000ull,MEM_RESERVE,PAGE_READWRITE));
    Check(base,"Reserve guest fixture");
    for(uint32_t page : {object,visual,0x30000u,0x82070000u,0x820C0000u})
        Check(VirtualAlloc(base+page,0x10000,MEM_COMMIT,PAGE_READWRITE),"Commit guest fixture");
    for(int rate : {30,60,120,144,240}) {
        native=true;rex::cvar::SetFlagByName("normalize_lift_fade","true");
        for(bool out : {false,true}) {
            Seed(base,out?1:0,out);
            for(int frame=1;frame<=rate/2;++frame) Near(Advance(base,1.f/rate),out ? std::max(0.f,1.f-2.f*frame/rate) : std::min(1.f,2.f*frame/rate));
            Near(Advance(base,1.f/rate),out?0:1);
            Check(getters==copies && getters==setters && getters==3u*(rate/2+1),"Every original transform boundary executes once");
        }
    }
    // Uneven retained millisecond updates preserve elapsed duration, including
    // a 100ms hitch sample and a zero interval which must freeze the fade.
    Seed(base,0,false);Near(Advance(base,0),0);
    unsigned total=0, index=0;const unsigned intervals[]={4,7,12,100,9,6,17};
    while(total<500) {unsigned ms=std::min(intervals[index++%7],500-total);total+=ms;Near(Advance(base,float(ms)*.001f),std::min(1.f,total*.002f));}
    // Original 120Hz behavior reaches full fade in 15 calls (125ms).
    Seed(base,0,false);native=false;
    for(int i=0;i<15;++i) Advance(base,1.f/120);
    Near(Load(base,visual+136),1);
    // Guard and 30Hz comparisons include the complete context, stack, object
    // and all attached visual memory, against the actual unhooked body.
    for(unsigned guard=0;guard<10;++guard) for(bool out : {false,true}) {
        Seed(base,.45f,out);native=true;rex::cvar::SetFlagByName("normalize_lift_fade","true");
        auto input=Context();
        if(guard==0) native=false;
        if(guard==1) rex::cvar::SetFlagByName("normalize_lift_fade","false");
        if(guard==2) input.lr=0x12345678;
        if(guard==3) {REX_STORE_U32(object,0x820C44C8);REX_STORE_U32(0x820C44C8+336,0x82100000);}
        if(guard==4) input.f31.f64=-.01;
        if(guard==5) input.f31.f64=std::numeric_limits<double>::quiet_NaN();
        if(guard==6) input.f31.f64=std::numeric_limits<double>::infinity();
        if(guard==7) input.f31.f64=.11;
        if(guard==8) Store(base,0x82072048,.05f);
        // guard 9 uses 1/30: normalization must be bit-identical to stock.
        std::array<uint8_t,4096> old_object,old_visual,old_stack,expected_object,expected_visual,expected_stack;
        std::memcpy(old_object.data(),base+object,4096);std::memcpy(old_visual.data(),base+visual,4096);std::memcpy(old_stack.data(),base+stack-4096,4096);
        auto expected=input;original_fade(expected,base);
        std::memcpy(expected_object.data(),base+object,4096);std::memcpy(expected_visual.data(),base+visual,4096);std::memcpy(expected_stack.data(),base+stack-4096,4096);
        std::memcpy(base+object,old_object.data(),4096);std::memcpy(base+visual,old_visual.data(),4096);std::memcpy(base+stack-4096,old_stack.data(),4096);
        getters=copies=setters=0;
        auto actual=input;sub_82531228(actual,base);
        Check(std::memcmp(&actual,&expected,sizeof(actual))==0,"Pass-through complete PPC context");
        Check(std::memcmp(base+object,expected_object.data(),4096)==0,"Pass-through complete object");
        Check(std::memcmp(base+visual,expected_visual.data(),4096)==0,"Pass-through attached visuals");
        Check(std::memcmp(base+stack-4096,expected_stack.data(),4096)==0,"Pass-through stack writes");
        Check(getters==3 && copies==3 && setters==3,"Wrapper forwards original exactly once");
    }
    // Empty attached visuals retain the original transform dispatch and skip
    // the fade hook and visual setters entirely.
    Seed(base,.25f,false);native=true;rex::cvar::SetFlagByName("normalize_lift_fade","true");
    for(unsigned offset : {2404u,2408u,2412u}) REX_STORE_U32(object+offset,0);
    auto empty=Context(1.f/240);sub_82531228(empty,base);
    Check(getters==3 && copies==3 && setters==0,"Empty attachment path preserved");
    Check(Load(base,visual+136)==.25f,"Detached visual untouched");
    // Record actual fade arithmetic on both paths and enforce the trace cap.
    if(argc>1) {
        rex::cvar::SetFlagByName("lift_fade_trace_path",argv[1]);
        for(unsigned frame=0;frame<8200;++frame) {
            Seed(base,.2f,bool(frame%2));native=frame>=64;
            Advance(base,1.f/120);
        }
    }
    VirtualFree(base,0,MEM_RELEASE);
    std::printf("PASS: generated cLift fade at 30/60/120/144/240Hz, variable dt, clamps, transforms and complete pass-through (%u checks)\n",checks);
}
'''
    program = program.replace('ORIGINAL_FUNCTION', original).replace('ACTUAL_FUNCTION', actual)
    with tempfile.TemporaryDirectory(prefix='crackdown-lift-') as directory:
        source, exe, trace = (Path(directory) / name for name in ('lift.cpp', 'lift.exe', 'trace.csv'))
        source.write_text(program, encoding='utf-8')
        subprocess.run([args.cxx, '-std=c++23', '-O2', '-msse4.1', '-DNDEBUG', '-D_DLL', '-D_MT',
                        '-Xclang', '--dependent-lib=msvcrt', '-DSPDLOG_FMT_EXTERNAL', '-DSPDLOG_COMPILED_LIB',
                        '-I', str(args.generated), '-I', str(ROOT), '-isystem', str(args.sdk / 'include'),
                        str(source), str(args.sdk / 'lib/rexruntime.lib'), str(args.sdk / 'lib/spdlog.lib'),
                        str(args.sdk / 'lib/fmt.lib'), '-o', str(exe), '-fuse-ld=lld-link'], check=True)
        environment = dict(os.environ)
        environment['PATH'] = str(args.sdk / 'bin') + os.pathsep + environment.get('PATH', '')
        subprocess.run([str(exe), str(trace)], env=environment, check=True, timeout=20)
        with trace.open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 8192, 'CSV must stop at its bounded row cap'
        for index, row in enumerate(rows):
            step = 1 / 15 if index < 64 else 2 / 120
            direction = -1 if index % 2 else 1
            assert row['normalized'] == str(int(index >= 64))
            assert row['fading_out'] == str(index % 2)
            assert abs(float(row['elapsed']) - 1 / 120) < 1e-8
            assert abs(float(row['after']) - float(row['before']) - direction * step) < 1e-7
        print('PASS: actual lift fade CSV, baseline/normalized intervals and 8,192-row cap')


if __name__ == '__main__':
    main()

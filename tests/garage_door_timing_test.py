"""Execute TU0's generated garage update and five-second timeout consumer.

The local generated bodies are inputs, not distributed game code. Base update,
actor lookup and position-query boundaries are stubbed. Timer arithmetic, timer
resets, timeout comparisons and door state transitions execute the real bodies.
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
    names = ('sub_8252EDA0', 'sub_8252F2E8')
    pattern = re.compile(r'DEFINE_REX_FUNC\(([^)]+)\) \{.*?\n\}', re.S)
    for path in args.generated.glob('crackdown_recomp.*.cpp'):
        for match in pattern.finditer(path.read_text(encoding='utf-8')):
            if match[1] in names:
                if match[1] in bodies:
                    raise AssertionError('Duplicate garage routine')
                bodies[match[1]] = match[0]
    if set(bodies) != set(names):
        raise AssertionError('Generate current TU0 code before garage tests')
    update, consumer = (bodies[name] for name in names)
    hook = '\tCrackdownGarageDoorTimerStep(ctx.f0);\n'
    if update.count(hook) != 1 or not re.search(
            r'ctx\.f0\.f64 = double\(temp\.f32\);\s*// fadds f0,f13,f0\s*'
            r'CrackdownGarageDoorTimerStep\(ctx\.f0\);\s*ctx\.f0\.f64 = double\(float\(ctx\.f13\.f64 \+ ctx\.f0\.f64\)\)', update):
        raise AssertionError('Hook must change only the consumed timer increment')
    original = update.replace(hook, '').replace('DEFINE_REX_FUNC(sub_8252EDA0)', 'REX_EXTERN(original_update)')
    actual = update.replace('DEFINE_REX_FUNC(sub_8252EDA0)', 'REX_EXTERN(__imp__sub_8252EDA0)')
    consumer = consumer.replace('DEFINE_REX_FUNC(sub_8252F2E8)', 'REX_EXTERN(sub_8252F2E8)')
    program = r'''
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "crackdown_funcs.h"
#include "src/garage_door_timing.cpp"
#include <array>
#include <cstdio>
#include <cstring>
#include <limits>
static bool native = true, inside = false;
bool crackdown::NativeTimingActive() { return native; }
static unsigned checks=0, base_calls=0, getters=0, proximity_calls=0;
static constexpr uint32_t object=0x10000, actor=0x20000, observer=0x28000, stack=0x3F000;
static void Check(bool valid,const char* message) {
    ++checks;
    if(!valid) { std::fprintf(stderr,"Garage timer: %s\n",message);std::exit(1); }
}
static float Load(uint8_t* base,uint32_t address) {return std::bit_cast<float>(REX_LOAD_U32(address));}
static void Store(uint8_t* base,uint32_t address,float value) {REX_STORE_U32(address,std::bit_cast<uint32_t>(value));}
static PPCContext Context(float dt=1.f/30) {
    PPCContext ctx{};ctx.fpscr.InitHost();ctx.r1.u32=stack;ctx.r3.u32=object;
    ctx.lr=0x82222222;ctx.f1.f64=double(dt);ctx.r30.u64=0xFEDCBA9876543210ull;ctx.r31.u64=0x123456789ABCDEF0ull;
    ctx.f29.f64=29.25;ctx.f30.f64=30.25;ctx.f31.f64=31.25;return ctx;
}
static void Indirect(PPCContext& ctx,uint8_t* base,uint32_t address) {
    ++getters;ctx.f1.f64=-27.5;
    if(address==0x82100000) { // Nearby actor's original position query.
        for(unsigned offset : {0u,4u,8u,12u}) Store(base,ctx.r3.u32+offset,0);
    } else if(address==0x82100010) { // Optional observer's update.
    } else if(address==0x82100020) { // Door state-3 virtual boundary.
        ctx.r3.u32=0;
    } else Check(false,"Unexpected indirect boundary");
}
#undef REX_CALL_INDIRECT_FUNC
#define REX_CALL_INDIRECT_FUNC(address) Indirect(ctx,base,address)
REX_EXTERN(sub_825184E8) { ++base_calls;ctx.f1.f64=123.5; }
REX_EXTERN(sub_8252F170) { ++proximity_calls;ctx.f1.f64=234.5; }
REX_EXTERN(sub_8252F488) { ctx.r3.u32=inside; }
REX_EXTERN(sub_825042B0) { ctx.r3.u32=actor; }
REX_EXTERN(sub_82130998) { ctx.r3.u32=0; }
REX_EXTERN(sub_82524490) {}
REX_EXTERN(sub_8252E8A0) { ctx.r3.u32=0; }
REX_EXTERN(sub_82524528) {}
REX_EXTERN(sub_8252EF20) { Check(false,"Unconfigured reset boundary"); }
CONSUMER_FUNCTION
ORIGINAL_FUNCTION
ACTUAL_FUNCTION
static void Seed(uint8_t* base,uint32_t state=1,float timer=0) {
    std::memset(base+object,0,4096);std::memset(base+actor,0,4096);std::memset(base+stack-4096,0xAA,4096);
    REX_STORE_U32(object,0x820C36F0);REX_STORE_U32(object+2368,state);Store(base,object+2404,timer);
    REX_STORE_U32(0x820C36F0+1224,0x82100020);
    REX_STORE_U32(actor+32,0x820C4000);REX_STORE_U32(0x820C4000+12,0x82100000);
    REX_STORE_U32(observer,0x820C4100);REX_STORE_U32(0x820C4100+8,0x82100010);
    Store(base,0x820ED704,1.f/30);Store(base,0x8207092C,5);Store(base,0x820722DC,0);
    Store(base,0x820722CC,1);Store(base,0x8207047C,.5f);Store(base,0x820EDB94,121);
    inside=false;native=true;rex::cvar::SetFlagByName("normalize_garage_door_timer","true");
    base_calls=proximity_calls=getters=0;
}
static float Advance(uint8_t* base,float dt) {
    auto ctx=Context(dt);sub_8252EDA0(ctx,base);
    Check(ctx.r1.u32==stack && ctx.lr==0x82222222,"Original stack and LR preservation");
    Check(ctx.r30.u64==0xFEDCBA9876543210ull && ctx.r31.u64==0x123456789ABCDEF0ull,"Original nonvolatile integer registers");
    Check(ctx.f29.f64==29.25 && ctx.f30.f64==30.25 && ctx.f31.f64==31.25,"Original nonvolatile float registers");
    return Load(base,object+2404);
}
int main(int argc,char** argv) {
    auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,0x86000000ull,MEM_RESERVE,PAGE_READWRITE));
    Check(base,"Reserve guest fixture");
    for(uint32_t page : {object,actor,0x30000u,0x82070000u,0x820C0000u,0x820E0000u,0x82520000u,0x82DE0000u})
        Check(VirtualAlloc(base+page,0x10000,MEM_COMMIT,PAGE_READWRITE),"Commit guest fixture");
    for(int rate : {30,60,120,144,240}) {
        Seed(base);
        for(int frame=1;frame<=rate;++frame) {
            auto value=Advance(base,1.f/rate);
            Check(std::abs(value-float(frame)/rate)<2e-5,"Timer measures elapsed seconds");
        }
        Check(base_calls==unsigned(rate) && proximity_calls==unsigned(rate),"Original base and proximity calls execute once");
        Seed(base,4);int expired=0;
        for(int frame=1;frame<=5*rate+2;++frame) {
            Advance(base,1.f/rate);
            if(REX_LOAD_U32(object+2368)==3) {expired=frame;break;}
            Check(REX_LOAD_U32(object+2368)==4,"Door state retained until actual timeout consumer expires");
        }
        Check(expired && float(expired)/rate>=4.999f && float(expired)/rate<=5.f+1.01f/rate,"Five-second state transition at every frame rate");
    }
    Seed(base);unsigned total=0,index=0;const unsigned intervals[]={4,7,12,100,9,6,17};
    Check(Advance(base,0)==0,"Zero retained time freezes timer");
    while(total<5000) {
        unsigned ms=std::min(intervals[index++%7],5000-total);total+=ms;
        Check(std::abs(Advance(base,float(ms)*.001f)-total*.001f)<5e-5,"Variable intervals sum to elapsed seconds");
    }
    Seed(base,4);native=false;int stock_expired=0;
    for(int frame=1;frame<=160;++frame) {
        Advance(base,1.f/120);
        if(REX_LOAD_U32(object+2368)==3) {stock_expired=frame;break;}
    }
    Check(stock_expired>=150 && stock_expired<=151,"Stock 120Hz timeout expires after about 1.25 seconds");
    // Authored resets and the optional observer still execute unchanged.
    Seed(base,0,4);Check(Advance(base,1.f/240)==0,"Closed-state timer reset");
    Check(REX_LOAD_U8(0x82DE3F57)==0,"Original closed-state global flag");
    Seed(base,4,4);inside=true;Check(Advance(base,1.f/240)==0,"Player-inside timer reset");
    Check(REX_LOAD_U32(object+2368)==4,"Player inside keeps door open");
    Seed(base,1);REX_STORE_U32(object+2112,observer);
    Check(std::abs(Advance(base,.05f)-.05f)<1e-7,"Retained incoming f1 survives optional observer and base clobbers");
    Seed(base,2);Advance(base,.05f);
    Check(REX_LOAD_U32(object+2368)==4 && REX_LOAD_U8(0x82DE3F57)==1,"Original opening-state transition and flag");
    // Compare the full PPC context, object and stack against unhooked TU0.
    for(unsigned guard=0;guard<9;++guard) for(uint32_t state : {0u,1u,2u,3u,4u,5u}) {
        Seed(base,state,.45f);auto input=Context();
        if(guard==0) native=false;
        if(guard==1) rex::cvar::SetFlagByName("normalize_garage_door_timer","false");
        if(guard==2) {REX_STORE_U32(object,0x820C38F0);REX_STORE_U32(0x820C38F0+1224,0x82100020);}
        if(guard==3) input.f1.f64=-.01;
        if(guard==4) input.f1.f64=std::numeric_limits<double>::quiet_NaN();
        if(guard==5) input.f1.f64=std::numeric_limits<double>::infinity();
        if(guard==6) input.f1.f64=.11;
        if(guard==7) Store(base,0x820ED704,.025f);
        // guard 8 normalizes 1/30: it must be bit-identical to original.
        std::array<uint8_t,4096> old_object,old_stack,expected_object,expected_stack;
        std::memcpy(old_object.data(),base+object,4096);std::memcpy(old_stack.data(),base+stack-4096,4096);
        auto expected=input;original_update(expected,base);
        std::memcpy(expected_object.data(),base+object,4096);std::memcpy(expected_stack.data(),base+stack-4096,4096);
        std::memcpy(base+object,old_object.data(),4096);std::memcpy(base+stack-4096,old_stack.data(),4096);
        base_calls=proximity_calls=0;auto actual=input;sub_8252EDA0(actual,base);
        Check(std::memcmp(&actual,&expected,sizeof(actual))==0,"Pass-through complete PPC context");
        Check(std::memcmp(base+object,expected_object.data(),4096)==0,"Pass-through complete object");
        Check(std::memcmp(base+stack-4096,expected_stack.data(),4096)==0,"Pass-through stack writes");
        Check(base_calls==1 && proximity_calls==1,"Original update forwarded once");
    }
    // Exercise optional CSV against the actual consumer, including its cap.
    if(argc>1) {
        rex::cvar::SetFlagByName("garage_door_trace_path",argv[1]);Seed(base);
        for(unsigned frame=0;frame<8200;++frame) {
            native=frame>=64;Advance(base,1.f/120);
        }
    }
    VirtualFree(base,0,MEM_RELEASE);
    std::printf("PASS: generated garage timer/timeout at 30/60/120/144/240Hz, resets, variable dt and pass-through (%u checks)\n",checks);
}
'''
    program = program.replace('CONSUMER_FUNCTION', consumer).replace('ORIGINAL_FUNCTION', original).replace('ACTUAL_FUNCTION', actual)
    with tempfile.TemporaryDirectory(prefix='crackdown-door-') as directory:
        source, exe, trace = (Path(directory) / name for name in ('door.cpp', 'door.exe', 'trace.csv'))
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
            expected = 1 / 30 if index < 64 else 1 / 120
            assert row['normalized'] == str(int(index >= 64))
            assert row['state_before'] == row['state_after'] == '1'
            assert abs(float(row['elapsed']) - 1 / 120) < 1e-8
            assert abs(float(row['after']) - float(row['before']) - expected) < 1e-5
        print('PASS: actual garage consumer CSV, baseline/normalized intervals and 8,192-row cap')


if __name__ == '__main__':
    main()

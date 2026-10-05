"""Execute locally generated low-detail car movement with elapsed-time hooks."""
import argparse
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
    movement = allocation = None
    for path in args.generated.glob('crackdown_recomp.*.cpp'):
        for m in re.finditer(r'DEFINE_REX_FUNC\(([^)]+)\) \{.*?\n\}', path.read_text(), re.S):
            if m[1] == 'sub_82333538':
                movement = m[0]
            elif m[1] == 'sub_82333310':
                allocation = m[0]
    if not movement or not allocation:
        raise AssertionError('Generate current TU0 code before running car tests.')
    for hook in ('Direction', 'Advance', 'Countdown'):
        if movement.count('CrackdownBackgroundCar' + hook + '(') != 1:
            raise AssertionError(f'Missing generated car hook: {hook}')
    if allocation.count('CrackdownBackgroundCarAllocated(') != 1:
        raise AssertionError('Missing slot allocation hook.')
    original = re.sub(r'^\s*CrackdownBackgroundCar\w+\([^\n]+\);\n', '', movement, flags=re.M)
    original = original.replace('DEFINE_REX_FUNC(sub_82333538)', 'DEFINE_REX_FUNC(original_car_update)')
    # The host wrapper is included in this fixture's translation unit. Provide
    # only its generated implementation here, avoiding a second weak wrapper.
    movement = movement.replace('DEFINE_REX_FUNC(sub_82333538)', 'REX_EXTERN(__imp__sub_82333538)')
    program = r'''
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "crackdown_funcs.h"
#include "src/crowd_timing.cpp"
#include <bit>
#include <cstdio>
#include <cstdlib>
#include <cstring>
static constexpr uint32_t actor=0x82FDF470,stack=0x10F000;
static unsigned checks=0,paths=0;
static void Check(bool value,const char* message) {
    ++checks;if(!value) {std::fprintf(stderr,"Background cars: %s\n",message);std::abort();}
}
static void Near(double value,double expected,const char* message,double epsilon=.004) {
    if(std::abs(value-expected)>epsilon)std::fprintf(stderr,"Got %.12f, expected %.12f\n",value,expected);
    Check(std::abs(value-expected)<=epsilon,message);
}
static void Store(uint32_t a,float v,uint8_t* base) {REX_STORE_U32(a,std::bit_cast<uint32_t>(v));}
static float Load(uint32_t a,uint8_t* base) {return std::bit_cast<float>(REX_LOAD_U32(a));}
static PPCContext Context() {PPCContext ctx{};ctx.fpscr.InitHost();return ctx;}
REX_EXTERN(__imp__sub_823315E0) {Check(false,"No pedestrian allocation");}
REX_EXTERN(__imp__sub_82330038) {Check(false,"No pedestrian update");}
REX_EXTERN(__imp__sub_82338220) {Check(false,"No pedestrian steering");}
REX_EXTERN(sub_82333458) {++paths;REX_STORE_U32(ctx.r4.u32+124,20);ctx.r3.u64=0;}
GUEST_MOVEMENT
ORIGINAL_MOVEMENT
static void Reset(uint8_t* base,uint32_t count=1000,float step=1,float multiplier=1) {
    std::memset(base+actor,0,480);
    REX_STORE_U32(0x82FDE4B4,0xACED0FF0);
    REX_STORE_U32(actor+124,count);REX_STORE_U32(actor+136,0x100200);
    REX_STORE_U32(actor+140,0x100300);REX_STORE_U32(actor+144,0x100400);
    for(uint32_t offset : {16u,32u}) {
        Store(actor+offset,1,base);Store(actor+offset+4,2,base);Store(actor+offset+8,3,base);
    }
    Store(actor+116,step,base);Store(actor+120,multiplier,base);
    REX_STORE_U32(0x82D9912C,0);
    PPCRegister object{};object.u32=actor;CrackdownBackgroundCarAllocated(object);
}
static void Tick(uint8_t* base,uint32_t ms,uint32_t address=actor) {
    REX_STORE_U32(0x82D99128,ms);REX_STORE_U32(0x82D9912C,REX_LOAD_U32(0x82D9912C)+ms);
    auto ctx=Context();ctx.r1.u32=stack;ctx.r3.u32=address;ctx.lr=0x12345678;
    sub_82333538(ctx,base);
}
int main() {
    auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,0x84000000ull,MEM_RESERVE,PAGE_READWRITE));
    Check(base!=nullptr,"Reserve mapping");
    for(auto a : {0x100000u,0x82060000u,0x82D90000u})
        Check(VirtualAlloc(base+a,0x10000,MEM_COMMIT,PAGE_READWRITE)!=nullptr,"Commit fixture page");
    Check(VirtualAlloc(base+0x82FD0000,0x90000,MEM_COMMIT,PAGE_READWRITE)!=nullptr,"Commit car pool");
    Store(0x8206F394,.1f,base);
    Check(rex::cvar::SetFlagByName("normalize_background_car_timing","true"),"Select car correction");
    // Car correction is independent of the pedestrian flag/pool signature.
    Check(rex::cvar::SetFlagByName("normalize_crowd_timing","false"),"Disable pedestrian correction");
    crackdown::SetCrowdNativeTiming(true);
    for(int hz : {30,60,120,144,240}) {
        for(float step : {1.0f/3, .75f}) for(float multiplier : {1.f,1.45f}) {
            Reset(base,1000,step,multiplier);uint32_t time=0;
            for(int frame=1;frame<=hz*2;++frame) {
                auto next=uint32_t(std::llround(frame*1000.0/hz));Tick(base,next-time);time=next;
            }
            Near(Load(actor+64,base),60*step*multiplier,"Authored cruise displacement follows elapsed time");
            Near(Load(actor+68,base),120*step*multiplier,"Y displacement follows elapsed time");
            Near(Load(actor+72,base),180*step*multiplier,"Z displacement follows elapsed time");
            Check(REX_LOAD_U32(actor+124)==940,"Two seconds consume 60 reference steps");
            Near(Load(actor+116,base),step,"Authored speed unchanged",0);
            Near(Load(actor+120,base),multiplier,"Authored multiplier unchanged",0);
            Near(Load(actor+48,base),step*multiplier,"Stored displacement remains nominal",.000001);
        }
        Reset(base);Store(actor+16,0,base);Store(actor+32,1,base);uint32_t time=0;
        for(int frame=1;frame<=hz;++frame) {
            auto next=uint32_t(std::llround(frame*1000.0/hz));Tick(base,next-time);time=next;
        }
        Near(Load(actor+32,base),std::pow(.9,30),"Direction response preserved over one second",.000001);
    }
    Reset(base);uint32_t elapsed=0;
    for(auto ms : {4u,17u,50u,3u,8u,99u,2u,37u}) {Tick(base,ms);elapsed+=ms;}
    Near(Load(actor+64,base),elapsed*.03,"Variable deltas consume retained time only");
    Check(REX_LOAD_U32(actor+124)==1000-uint32_t(elapsed*.03),"Fractional countdown retained");
    Reset(base,1);Tick(base,100);
    Near(Load(actor+64,base),1,"Cannot overshoot final segment step");
    Check(REX_LOAD_U32(actor+124)==0,"No countdown underflow");
    auto before=paths;Tick(base,4);
    Check(paths==before+1 && REX_LOAD_U32(actor+124)==20,"Original path initialization still runs");
    Near(Load(actor+64,base),1,"Initialization retains no-movement behavior");
    Reset(base,100);for(int n=0;n<3;++n)Tick(base,10);
    PPCRegister object{};object.u32=actor;CrackdownBackgroundCarAllocated(object);Tick(base,10);
    Check(REX_LOAD_U32(actor+124)==100,"Allocation clears prior fractional progress");
    REX_STORE_U32(actor+144,0x100500);Tick(base,10);
    Check(REX_LOAD_U32(actor+124)==100,"New route clears fractional progress");
    REX_STORE_U32(actor+124,50);Tick(base,10);
    Check(REX_LOAD_U32(actor+124)==50,"External countdown rewrite clears fractional progress");
    Reset(base);Store(0x82FDE4B0+0x70000+30176+12,7.5f,base);Tick(base,10);
    Near(Load(actor+76,base),7.5,"Original W displacement remains unscaled",0);
    Store(0x82FDE4B0+0x70000+30176+12,0,base);
    // Paired original bodies run from identical full context and guest state.
    for(unsigned guard=0;guard<8;++guard) {
        crackdown::SetCrowdNativeTiming(true);
        Check(rex::cvar::SetFlagByName("normalize_background_car_timing","true"),"Reset correction flag");
        Reset(base);REX_STORE_U32(0x82D99128,9);
        if(guard==0)crackdown::SetCrowdNativeTiming(false);
        if(guard==1)Check(rex::cvar::SetFlagByName("normalize_background_car_timing","false"),"Opt out");
        if(guard==2)REX_STORE_U32(0x82D99128,0);
        if(guard==3)REX_STORE_U32(0x82D99128,101);
        if(guard==4)REX_STORE_U32(0x82FDE4B4,0);
        uint32_t address=guard==5?0x100800:actor;
        if(guard==5)std::memcpy(base+address,base+actor,480);
        if(guard==6)REX_STORE_U32(actor+124,0); // Route initialization bypasses all arithmetic hooks.
        if(guard==7)REX_STORE_U32(actor+124,0x80000001);
        auto input=Context();input.r1.u32=stack;input.r3.u32=address;input.lr=0x12345678;
        input.r28.u64=0x123456789ABCDEFull;input.f25.f64=17.25;
        std::array<uint8_t,480> old{},expected_memory{};
        std::array<uint8_t,256> old_stack{},expected_stack{};
        std::memcpy(old.data(),base+address,old.size());std::memcpy(old_stack.data(),base+stack-256,old_stack.size());
        auto expected=input;original_car_update(expected,base);
        std::memcpy(expected_memory.data(),base+address,old.size());std::memcpy(expected_stack.data(),base+stack-256,old_stack.size());
        std::memcpy(base+address,old.data(),old.size());std::memcpy(base+stack-256,old_stack.data(),old_stack.size());
        auto actual=input;sub_82333538(actual,base);
        Check(std::memcmp(&actual,&expected,sizeof(actual))==0,"Pass-through preserves complete context");
        Check(std::memcmp(base+address,expected_memory.data(),old.size())==0,"Pass-through preserves complete car memory");
        Check(std::memcmp(base+stack-256,expected_stack.data(),old_stack.size())==0,"Pass-through preserves stack memory");
    }
    VirtualFree(base,0,MEM_RELEASE);
    std::printf("PASS: actual TU0 car movement, direction, countdown, routes, reuse and guards (%u checks)\n",checks);
}
'''
    program = program.replace('GUEST_MOVEMENT', movement).replace('ORIGINAL_MOVEMENT', original)
    with tempfile.TemporaryDirectory(prefix='crackdown-background-cars-') as directory:
        folder = Path(directory)
        source, exe = folder / 'cars.cpp', folder / 'cars.exe'
        source.write_text(program)
        subprocess.run([args.cxx, '-std=c++23', '-O2', '-msse4.1', '-DNDEBUG', '-D_DLL', '-D_MT',
                        '-Xclang', '--dependent-lib=msvcrt', '-DSPDLOG_FMT_EXTERNAL', '-DSPDLOG_COMPILED_LIB',
                        '-I', str(args.generated), '-I', str(ROOT), '-isystem', str(args.sdk / 'include'),
                        str(source), str(args.sdk / 'lib/rexruntime.lib'), str(args.sdk / 'lib/spdlog.lib'),
                        str(args.sdk / 'lib/fmt.lib'), '-o', str(exe), '-fuse-ld=lld-link'], check=True)
        env = dict(os.environ)
        env['PATH'] = str(args.sdk / 'bin') + os.pathsep + env.get('PATH', '')
        subprocess.run([str(exe)], env=env, check=True, timeout=20)


if __name__ == '__main__':
    main()

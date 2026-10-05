"""Execute actual generated crowd movement/animation with native timing hooks.

Only path initialization, clip evaluation and the parent dispatch boundaries are
stubbed. Guest-derived functions remain local generated input, never fixtures
copied into the repository.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
NAMES = ('sub_82331198', 'sub_82330888', '__savegprlr_26', '__restgprlr_26')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generated', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--cxx', required=True)
    args = parser.parse_args()
    bodies = {}
    pattern = re.compile(r'DEFINE_REX_FUNC\(([^)]+)\) \{.*?\n\}', re.S)
    for path in args.generated.glob('crackdown_recomp.*.cpp'):
        for match in pattern.finditer(path.read_text(encoding='utf-8')):
            if match[1] in NAMES:
                if match[1] in bodies:
                    raise AssertionError(f'Duplicate guest function: {match[1]}')
                bodies[match[1]] = match[0]
    if set(bodies) != set(NAMES):
        raise AssertionError('Generate current TU0 code before running crowd tests.')
    for hook in ('CrackdownCrowdAdvance', 'CrackdownCrowdCountdown'):
        if bodies['sub_82331198'].count(hook + '(') != 1:
            raise AssertionError(f'Expected one generated movement hook: {hook}')
    if bodies['sub_82330888'].count('CrackdownCrowdAnimation(') != 1:
        raise AssertionError('Expected one generated animation hook.')
    originals = []
    for name in NAMES[:2]:
        original = re.sub(r'^\s*CrackdownCrowd\w+\([^\n]+\);\n', '', bodies[name], flags=re.M)
        originals.append(original.replace(f'DEFINE_REX_FUNC({name})', f'DEFINE_REX_FUNC(original_{name})'))
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
#include <vector>
REX_EXTERN(sub_82330038);
REX_EXTERN(sub_82338220);
REX_EXTERN(sub_823315E0);
static constexpr uint32_t actor=0x82E64090, stack=0x10F000;
static unsigned mode=0, path_calls=0, evaluations=0, parent_calls=0, checks=0;
static uint32_t allocation=actor;
static uint8_t* mapping=nullptr;
static LONG WINAPI ReportException(EXCEPTION_POINTERS* info) {
    auto* record=info->ExceptionRecord;
    std::fprintf(stderr,"Exception %08lx at %p, guest access %llx\n",record->ExceptionCode,
        record->ExceptionAddress,record->NumberParameters>1?record->ExceptionInformation[1]-uintptr_t(mapping):0);
    return EXCEPTION_EXECUTE_HANDLER;
}
static void Check(bool value,const char* message) {
    ++checks;
    if(!value) {std::fprintf(stderr,"Crowd timing: %s\n",message);std::abort();}
}
static void Near(double value,double expected,const char* message,double epsilon=.004) {
    if(std::abs(value-expected)>epsilon) std::fprintf(stderr,"Got %.12f, expected %.12f\n",value,expected);
    Check(std::abs(value-expected)<=epsilon,message);
}
static void Store(uint32_t address,float value,uint8_t* base) {REX_STORE_U32(address,std::bit_cast<uint32_t>(value));}
static float Load(uint32_t address,uint8_t* base) {return std::bit_cast<float>(REX_LOAD_U32(address));}
static PPCContext Context() {PPCContext ctx{};ctx.fpscr.InitHost();return ctx;}
REX_EXTERN(sub_82331228) {++path_calls;}
REX_EXTERN(sub_82330E08) {++path_calls;REX_STORE_U32(ctx.r4.u32+176,20);}
REX_EXTERN(sub_823401D0) {Check(false,"Unexpected clip reset boundary");}
REX_EXTERN(sub_8233B628) {++evaluations;}
REX_EXTERN(sub_8233B948) {++evaluations;}
GUEST_FUNCTIONS
ORIGINAL_FUNCTIONS
REX_EXTERN(__imp__sub_823315E0) {ctx.r3.u32=allocation;}
REX_EXTERN(__imp__sub_82333538) {Check(false,"Pedestrian fixture cannot update background cars");}
REX_EXTERN(__imp__sub_82330038) {
    ++parent_calls;
    if(mode==1) sub_82331198(ctx,base);
    else if(mode==2) sub_82330888(ctx,base);
    else {ctx.r3.u64=0x123456789ABCDEF0ull;ctx.f1.f64=47.25;}
}
REX_EXTERN(__imp__sub_82338220) {++parent_calls;ctx.f20.f64=.05;CrackdownCrowdSteering(ctx.f20);}

static void Reset(uint8_t* base,uint32_t count=1000,float step=1,float clip=100,unsigned lod=3) {
    std::memset(base+actor,0,672);
    REX_STORE_U32(0x82E61D30,0xACED0FF0);
    REX_STORE_U32(actor+176,count);
    REX_STORE_U32(actor+160,0x100200);REX_STORE_U32(actor+164,0x100300);
    Store(actor+112,step,base);Store(actor+116,2*step,base);Store(actor+120,3*step,base);
    Store(actor+184,.75f,base);
    REX_STORE_U32(actor+544,0x100500);REX_STORE_U32(actor+576,0x100600);
    REX_STORE_U32(actor+584,0x100900);
    REX_STORE_U16(0x100500+14,lod);REX_STORE_U8(actor+660,lod);Store(0x100600,clip,base);
    REX_STORE_U32(0x83055AB0+12292,0x100600);
    REX_STORE_U32(0x82E61D30+0xF9CD0,1); // LOD4 registry consumed from its end.
    REX_STORE_U32(0x82D9912C,0);
    auto ctx=Context();sub_823315E0(ctx,base);
}
static void Tick(uint8_t* base,uint32_t milliseconds,unsigned selected,uint32_t address=actor) {
    mode=selected;REX_STORE_U32(0x82D99128,milliseconds);
    REX_STORE_U32(0x82D9912C,REX_LOAD_U32(0x82D9912C)+milliseconds);
    if(selected==2) REX_STORE_U32(0x82E61D30+0xF9CD0,1);
    auto ctx=Context();ctx.r1.u32=stack;ctx.r3.u32=address;ctx.lr=0x12345678;
    auto calls=parent_calls;sub_82330038(ctx,base);
    Check(parent_calls==calls+1,"Parent forwards original exactly once");
}
int main() {
    auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,0x84000000ull,MEM_RESERVE,PAGE_READWRITE));
    mapping=base;SetUnhandledExceptionFilter(ReportException);
    Check(base!=nullptr,"Reserve sparse guest mapping");
    for(auto page : {0x100000u,0x82070000u,0x820E0000u,0x82D90000u,0x83050000u,0x83070000u})
        Check(VirtualAlloc(base+page,0x10000,MEM_COMMIT,PAGE_READWRITE)!=nullptr,"Commit fixture page");
    Check(VirtualAlloc(base+0x82E60000,0x110000,MEM_COMMIT,PAGE_READWRITE)!=nullptr,"Commit crowd fixture pool");
    Store(0x820722CC,1,base);Store(0x820722DC,0,base);Store(0x820ED7CC,.0333333f,base);
    Check(rex::cvar::SetFlagByName("normalize_crowd_timing","true"),"Select correction");
    crackdown::SetCrowdNativeTiming(true);
    for(auto hz : {30,60,120,144,240}) {
        for(float nominal : {.043f,.186666667f}) {
            Reset(base,1000,nominal);
            uint32_t time=0;
            for(int frame=1;frame<=hz*2;++frame) {
                auto next=uint32_t(std::llround(frame*1000.0/hz));
                Tick(base,next-time,1);time=next;
            }
            Near(Load(actor+128,base),60*nominal,"Movement depends on elapsed time, including authored run multiplier");
            Near(Load(actor+132,base),120*nominal,"Y movement scales with elapsed time");
            Near(Load(actor+136,base),180*nominal,"Z movement scales with elapsed time");
            Check(REX_LOAD_U32(actor+176)==940,"Path countdown preserves 60 reference steps in two seconds");
        }
        for(unsigned lod : {3u,4u,5u}) {
        Reset(base,1000,1,100,lod);uint32_t time=0;auto before=evaluations;
        for(int frame=1;frame<=hz*2;++frame) {
            auto next=uint32_t(std::llround(frame*1000.0/hz));
            Tick(base,next-time,2);time=next;
        }
        Near(Load(actor+228,base),1.5,"Animation phase advances with elapsed time and multiplier",.00003);
        Check(evaluations==before+hz*2,"Original clip evaluation still runs every native update");
        }
        double response=1;uint32_t time=0;
        for(int frame=1;frame<=hz;++frame) {
            auto next=uint32_t(std::llround(frame*1000.0/hz));
            REX_STORE_U32(0x82D99128,next-time);time=next;
            auto ctx=Context();sub_82338220(ctx,base);response*=1-ctx.f20.f64;
        }
        Near(response,std::pow(.95,30),"Steering response invariant over one second",.000001);
    }
    Reset(base);uint32_t total=0;
    for(auto ms : {4u,17u,50u,3u,8u,99u,2u,37u}) {Tick(base,ms,1);total+=ms;}
    Near(Load(actor+128,base),total*.03,"Variable deltas, including hitches, integrate retained time only");
    Check(REX_LOAD_U32(actor+176)==1000-uint32_t(total*.03),"Variable-delta countdown retains fractional progress");
    Reset(base,1);Tick(base,100,1);
    Near(Load(actor+128,base),1,"Long update cannot overshoot remaining segment");
    Check(REX_LOAD_U32(actor+176)==0,"Segment reaches zero without unsigned underflow");
    auto paths=path_calls;Tick(base,4,1);
    Check(path_calls==paths+2 && REX_LOAD_U32(actor+176)==20,"Original path initialization remains intact");
    Near(Load(actor+128,base),1,"Path initialization retains its original no-movement behavior");
    Reset(base,100);Tick(base,10,1);Tick(base,10,1);Tick(base,10,1);
    Check(REX_LOAD_U32(actor+176)==100,"Substep fractions do not prematurely exhaust path");
    auto allocated=Context();sub_823315E0(allocated,base);Tick(base,10,1);
    Check(REX_LOAD_U32(actor+176)==100,"Recycled slot does not inherit old fractional progress");
    REX_STORE_U32(actor+160,0x100700);Tick(base,10,1);
    Check(REX_LOAD_U32(actor+176)==100,"New path resets fractional progress");
    REX_STORE_U32(actor+176,50);Tick(base,10,1);
    Check(REX_LOAD_U32(actor+176)==50,"Guest countdown rewrite resets fractional progress");
    Reset(base,100);Store(actor+124,7.5f,base);Tick(base,10,1);
    Near(Load(actor+140,base),7.5,"VMX W component is not scaled",0);
    Reset(base,1000,1,.25f);
    for(unsigned frame=0;frame<20;++frame) Tick(base,10,2);
    Near(Load(actor+228,base),.15,"Clip wrap remains evaluated by original routine",.000003);

    // Complete context and object/stack memory compare against hook-free guest
    // bodies for original timing, explicit opt-out and malformed clock samples.
    for(unsigned lod : {3u,4u,5u}) for(unsigned selected : {1u,2u}) for(unsigned guard=0;guard<6;++guard) {
        crackdown::SetCrowdNativeTiming(true);Check(rex::cvar::SetFlagByName("normalize_crowd_timing","true"),"Reset mode");
        Reset(base,1000,1,100,lod);mode=selected;REX_STORE_U32(0x82D99128,9);
        if(guard==0) crackdown::SetCrowdNativeTiming(false);
        if(guard==1) Check(rex::cvar::SetFlagByName("normalize_crowd_timing","false"),"Select opt-out");
        if(guard==2) REX_STORE_U32(0x82D99128,0);
        if(guard==3) REX_STORE_U32(0x82D99128,101);
        if(guard==4) REX_STORE_U32(0x82E61D30,0);
        uint32_t address=guard==5?0x100800:actor;
        if(guard==5) std::memcpy(base+address,base+actor,672);
        auto input=Context();input.r1.u32=stack;input.r3.u32=address;input.lr=0x12345678;
        input.r28.u64=0x123456789ABCDEFull;input.f25.f64=17.25;
        std::array<uint8_t,672> before{},expected_object{};
        std::array<uint8_t,512> stack_before{},stack_expected{};
        // Animation also appends to the crowd's evaluation registry. Restore
        // that shared state before comparing a second invocation.
        std::vector<uint8_t> pool_before(0x110000),pool_expected(0x110000);
        std::memcpy(pool_before.data(),base+0x82E60000,pool_before.size());
        std::memcpy(before.data(),base+address,672);std::memcpy(stack_before.data(),base+stack-512,512);
        auto expected=input;
        if(selected==1) original_sub_82331198(expected,base);else original_sub_82330888(expected,base);
        std::memcpy(pool_expected.data(),base+0x82E60000,pool_expected.size());
        std::memcpy(expected_object.data(),base+address,672);std::memcpy(stack_expected.data(),base+stack-512,512);
        std::memcpy(base+address,before.data(),672);std::memcpy(base+stack-512,stack_before.data(),512);
        std::memcpy(base+0x82E60000,pool_before.data(),pool_before.size());
        auto actual=input;sub_82330038(actual,base);
        if(std::memcmp(&actual,&expected,sizeof(actual))!=0) {
            std::fprintf(stderr,"Context mismatch: mode %u guard %u\n",selected,guard);
            auto* a=reinterpret_cast<uint8_t*>(&actual);auto* e=reinterpret_cast<uint8_t*>(&expected);
            for(size_t i=0;i<sizeof(actual);++i) if(a[i]!=e[i])
                std::fprintf(stderr,"  byte %zu: actual %02x expected %02x\n",i,a[i],e[i]);
        }
        Check(std::memcmp(&actual,&expected,sizeof(actual))==0,"Pass-through preserves complete PPC context");
        Check(std::memcmp(base+address,expected_object.data(),672)==0,"Pass-through preserves complete object memory");
        Check(std::memcmp(base+stack-512,stack_expected.data(),512)==0,"Pass-through preserves original stack writes");
        Check(std::memcmp(base+0x82E60000,pool_expected.data(),pool_expected.size())==0,"Pass-through preserves complete crowd pool and evaluation registry");
    }
    crackdown::SetCrowdNativeTiming(false);mode=0;
    auto untouched=Context();sub_82330038(untouched,nullptr);
    Check(untouched.r3.u64==0x123456789ABCDEF0ull && untouched.f1.f64==47.25,"Disabled parent forwards without guest reads");
    VirtualFree(base,0,MEM_RELEASE);
    std::printf("PASS: actual TU0 crowd movement, countdown, animation, steering, slot/path resets and guards (%u checks)\n",checks);
}
'''
    program = program.replace('GUEST_FUNCTIONS', '\n'.join(bodies[name] for name in NAMES))
    program = program.replace('ORIGINAL_FUNCTIONS', '\n'.join(originals))
    with tempfile.TemporaryDirectory(prefix='crackdown-crowd-') as directory:
        folder = Path(directory)
        source, exe = folder / 'crowd.cpp', folder / 'crowd.exe'
        source.write_text(program, encoding='utf-8')
        subprocess.run([args.cxx, '-std=c++23', '-O2', '-msse4.1', '-DNDEBUG', '-D_DLL', '-D_MT',
                        '-Xclang', '--dependent-lib=msvcrt', '-DSPDLOG_FMT_EXTERNAL', '-DSPDLOG_COMPILED_LIB',
                        '-I', str(args.generated), '-I', str(ROOT), '-isystem', str(args.sdk / 'include'),
                        str(source), str(args.sdk / 'lib/rexruntime.lib'), str(args.sdk / 'lib/spdlog.lib'),
                        str(args.sdk / 'lib/fmt.lib'), '-o', str(exe), '-fuse-ld=lld-link'], check=True)
        environment = dict(os.environ)
        environment['PATH'] = str(args.sdk / 'bin') + os.pathsep + environment.get('PATH', '')
        subprocess.run([str(exe)], env=environment, check=True, timeout=20)


if __name__ == '__main__':
    main()

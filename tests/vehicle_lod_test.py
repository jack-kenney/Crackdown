"""Run the actual TU0 LOD selector and availability mapper through our hook.

The bounding-box transformation boundary is identity for these fixtures. All
distance comparisons, mesh selection, availability fallback and blend arithmetic
come from extracted guest code. This does not measure runtime performance.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile

FUNCTIONS = ("sub_823B6518", "sub_823B68C0", "__savegprlr_27", "__restgprlr_27")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generated', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--cxx', required=True)
    args = parser.parse_args()
    found = {name: [] for name in FUNCTIONS}
    pattern = re.compile(r'DEFINE_REX_FUNC\(([^)]+)\) \{.*?\n\}', re.S)
    for path in args.generated.glob('crackdown_recomp.*.cpp'):
        for match in pattern.finditer(path.read_text(encoding='utf-8')):
            if match[1] in found:
                body = match[0]
                if match[1] == 'sub_823B6518':
                    body = body.replace('DEFINE_REX_FUNC(sub_823B6518)',
                                        'DEFINE_REX_FUNC(__imp__sub_823B6518)')
                found[match[1]].append(body)
    for name, bodies in found.items():
        if len(bodies) != 1:
            raise AssertionError(f'Expected one TU0 implementation: {name}')
    root = Path(__file__).resolve().parents[1]
    program = r'''
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "crackdown_funcs.h"
#include <rex/cvar.h>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
static void Check(bool ok,const char* message) {
    if (!ok) { std::fprintf(stderr,"FAIL: %s\n",message);std::exit(1); }
}
// Identity transform: fixture bbox already occupies its world coordinates.
REX_EXTERN(sub_823C12A8) {}
#undef DEFINE_REX_FUNC
#define DEFINE_REX_FUNC(name) REX_EXTERN(name)
GUEST_FUNCTIONS
#include "HOOK"
int main() {
    auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,0x84000000ull,MEM_RESERVE,PAGE_READWRITE));
    Check(base,"Guest memory reserve");
    for (uint32_t address : {0x10000u,0x20000u,0x30000u,0x82070000u,0x82BA0000u,0x82C40000u,0x82DE0000u})
        Check(VirtualAlloc(base+address,0x10000,MEM_COMMIT,PAGE_READWRITE),"Fixture commit");
    constexpr uint32_t clump=0x10000,bbox=0x12000,camera=0x14000,table=0x16000,output=0x18000;
    auto Float=[&](uint32_t address,float value) { REX_STORE_U32(address,std::bit_cast<uint32_t>(value)); };
    REX_STORE_U32(0x82DE2704,table);
    REX_STORE_U32(0x82BA9060,0); // Render buffer index.
    Float(0x82BA9054,1); // Camera-distance scale.
    Float(0x8207047C,.5f);Float(0x820722CC,1);Float(0x820722DC,0); // Guest constants.
    Float(0x82C43080,0);Float(0x82C43084,1); // Guest clamp constants.
    REX_STORE_U32(clump+16+784,bbox);
    REX_STORE_U8(clump+817,1);REX_STORE_U8(clump+818,15); // All four mesh LODs present.
    for (uint32_t category=0;category<5;++category) {
        Float(table+36+category*20,50);Float(table+40+category*20,70);
        Float(table+44+category*20,150);Float(table+48+category*20,0);
        Float(table+52+category*20,10);
    }
    Float(0x82BAA3B8,25);REX_STORE_U8(0x82BAA3B4,1); // Observed native defaults.
    base[0x82BAA3B5]=0xA5;base[0x82BAA3B6]=0x5A;base[0x82BAA3B7]=0x7F;
    auto Context=[&](uint32_t category,float distance) {
        REX_STORE_U32(clump+1680,category);Float(camera+48,distance);
        PPCContext ctx{};ctx.r1.u32=0x3F000;ctx.lr=0x12345678;
        ctx.r3.u32=camera;ctx.r4.u32=clump;
        ctx.r5.u32=output;ctx.r6.u32=output+4;ctx.r7.u32=output+8;
        return ctx;
    };
    auto Run=[&](bool hooked,uint32_t category,float distance) {
        auto ctx=Context(category,distance);
        if(hooked)sub_823B6518(ctx,base);else __imp__sub_823B6518(ctx,base);
        return ctx;
    };
    auto First=[&] { return int32_t(REX_LOAD_U32(output)); };
    auto Second=[&] { return int32_t(REX_LOAD_U32(output+4)); };
    Check(!rex::cvar::SetFlagByName("vehicle_lod1_distance","-1"),"Negative distance accepted");
    Check(!rex::cvar::SetFlagByName("vehicle_lod1_distance","101"),"Excessive distance accepted");
    for (unsigned category=0;category<5;++category) for(float distance : {10.f,20.f,30.f,60.f,90.f,170.f}) {
        const auto original=Run(false,category,distance);
        unsigned char expected[12];std::memcpy(expected,base+output,12);
        const auto hooked=Run(true,category,distance);
        Check(std::memcmp(expected,base+output,12)==0,"Default changed selector outputs");
        Check(std::memcmp(&original,&hooked,sizeof(original))==0,"Default changed guest registers");
    }
    Check(rex::cvar::SetFlagByName("vehicle_lod1_distance","15"),"Could not enable distance");
    Run(true,3,30);
    Check(REX_LOAD_U32(0x82BAA3B8)==std::bit_cast<uint32_t>(25.f),"Non-vehicle initialized override");
    Run(true,2,30);
    Check(REX_LOAD_U32(0x82BAA3B8)==std::bit_cast<uint32_t>(15.f),"Override not installed");
    Check(First()==1 && Second()==1,"Vehicle did not select lower-detail mesh earlier");
    Check(base[0x82BAA3B5]==0xA5 && base[0x82BAA3B6]==0x5A && base[0x82BAA3B7]==0x7F,"Adjacent globals modified");
    for (unsigned category : {0u,1u,3u,4u}) {
        Run(true,category,30);Check(First()==0 && Second()==0,"Other mesh category changed");
    }
    Run(true,2,10);Check(First()==0 && Second()==0,"Nearby vehicle detail changed");
    Run(true,2,20);Check(First()==0 && Second()==1,"Transition lost native mesh blending");
    Check(std::abs(std::bit_cast<float>(REX_LOAD_U32(output+8))-.5f)<.001f,"Wrong blend weight");
    Run(true,2,90);Check(First()==2 && Second()==2,"Later transition changed");
    Run(true,2,170);Check(First()==3 && Second()==3,"Native far mesh changed");
    REX_STORE_U8(clump+818,1); // Only detailed mesh is available.
    Run(true,2,30);Check(First()==0 && Second()==0,"Missing lower mesh did not fall back");
    Check(rex::cvar::SetFlagByName("vehicle_lod1_distance","20"),"Could not set another startup value");
    Run(true,2,30);
    Check(REX_LOAD_U32(0x82BAA3B8)==std::bit_cast<uint32_t>(15.f),"Restart-only override reapplied per clump");
    VirtualFree(base,0,MEM_RELEASE);
    std::puts("PASS: actual TU0 selector, original defaults/registers, category guard, native blends/fallback, later distances, one-time override");
}
'''
    with tempfile.TemporaryDirectory(prefix='crackdown-vehicle-lod-') as directory:
        folder = Path(directory)
        source, exe = folder/'vehicle_lod.cpp', folder/'vehicle_lod.exe'
        source.write_text(program.replace('GUEST_FUNCTIONS', '\n'.join(found[n][0] for n in FUNCTIONS))
                          .replace('HOOK', (root/'experiments/vehicle_lod.cpp').as_posix()), encoding='utf-8')
        subprocess.run([args.cxx,'-std=c++23','-O2','-msse4.1','-DNDEBUG','-D_DLL','-D_MT',
                        '-Xclang','--dependent-lib=msvcrt','-DSPDLOG_FMT_EXTERNAL','-DSPDLOG_COMPILED_LIB',
                        '-I',str(args.generated),'-isystem',str(args.sdk/'include'),str(source),
                        str(args.sdk/'lib/rexruntime.lib'),str(args.sdk/'lib/spdlog.lib'),
                        str(args.sdk/'lib/fmt.lib'),'-o',str(exe),'-fuse-ld=lld-link'],check=True)
        environment = dict(os.environ)
        environment['PATH'] = str(args.sdk/'bin')+os.pathsep+environment.get('PATH','')
        subprocess.run([str(exe)],env=environment,check=True,timeout=15)


if __name__ == '__main__':
    main()

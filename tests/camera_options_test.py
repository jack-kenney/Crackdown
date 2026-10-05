"""Execute the actual TU0 camera snapshot routine through the production hook."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generated', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--cxx', required=True)
    args = parser.parse_args()
    names = ('sub_82284FA0', 'sub_82296748', 'sub_82295D10', 'sub_822969E8', 'sub_82378D10')
    pattern = re.compile(r'DEFINE_REX_FUNC\((' + '|'.join(names) + r')\) \{.*?\n\}', re.S)
    bodies = {}
    for path in args.generated.glob('crackdown_recomp.*.cpp'):
        for match in pattern.finditer(path.read_text()):
            if match[1] in bodies:
                raise AssertionError('Duplicate TU0 camera routine: ' + match[1])
            body = match[0].replace('DEFINE_REX_FUNC(' + match[1] + ')',
                                  'REX_EXTERN(' + ('__imp__' if match[1] == names[0] else '') + match[1] + ')')
            # The fixture supplies a camera transform in place of the two
            # engine virtual getters; all projection/frustum arithmetic is real.
            bodies[match[1]] = body.replace('REX_CALL_INDIRECT_FUNC(ctx.ctr.u32);',
                                           'FixtureTransform(ctx, base);')
    if set(bodies) != set(names):
        raise AssertionError('Missing TU0 camera projection routine')
    hook = Path(__file__).resolve().parents[1] / 'src/camera_options.cpp'
    program = r'''
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "crackdown_pch.h"
#include <rex/cvar.h>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
static void Check(bool ok, const char* message) {
    if (!ok) { std::fprintf(stderr,"%s\n",message); std::exit(1); }
}
static double tangentInput;
REX_EXTERN(sub_82AB9998);
REX_EXTERN(sub_82378D10);
REX_EXTERN(__imp__sub_82AB9998) {
    tangentInput = ctx.f1.f64;
    ctx.f1.f64 = std::tan(ctx.f1.f64);
}
// Engine register-save helpers and the virtual transform getters are outside
// this math fixture. Snapshot checks still compare the full guest context.
REX_EXTERN(__savegprlr_24) {}
REX_EXTERN(__restgprlr_24) {}
static void FixtureTransform(PPCContext& ctx, uint8_t*) { ctx.r3.u32 += 272; }
REX_EXTERN(sub_8213C980) { std::memcpy(base+ctx.r3.u32,base+ctx.r4.u32,64); }
GUEST_FUNCTION
#include "HOOK"
static void StoreFloat(uint8_t* base,uint32_t address,float value) {
    REX_STORE_U32(address,std::bit_cast<uint32_t>(value));
}
static float LoadFloat(uint8_t* base,uint32_t address) {
    return std::bit_cast<float>(REX_LOAD_U32(address));
}
static PPCContext NewContext() {
    PPCContext ctx{};ctx.fpscr.InitHost();return ctx;
}
int main() {
    auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr,0x84000000ull,MEM_RESERVE,PAGE_READWRITE));
    Check(base,"Guest memory reservation failed");
    Check(VirtualAlloc(base+0x10000,0x10000,MEM_COMMIT,PAGE_READWRITE),"Camera fixture commit failed");
    Check(VirtualAlloc(base+0x82DE0000,0x10000,MEM_COMMIT,PAGE_READWRITE),"Globals fixture commit failed");
    for (uint32_t page : {0x82060000u,0x82070000u,0x820E0000u,0x82BA0000u})
        Check(VirtualAlloc(base+page,0x10000,MEM_COMMIT,PAGE_READWRITE),"Projection constants commit failed");
    constexpr uint32_t manager=0x10000,camera=0x12000,holder=manager+272;
    REX_STORE_U32(0x82DE2574,manager);
    REX_STORE_U32(holder,camera);
    REX_STORE_U32(camera+6500,3);
    REX_STORE_U32(camera+6508,0);
    REX_STORE_U32(camera+6520,std::bit_cast<uint32_t>(55.0f));
    REX_STORE_U32(camera+6528,std::bit_cast<uint32_t>(1.0f));
    REX_STORE_U32(camera+6568,std::bit_cast<uint32_t>(0.05f));
    REX_STORE_U32(camera+6572,std::bit_cast<uint32_t>(20000.0f));
    for (unsigned i=0;i<64;++i) base[camera+272+i]=uint8_t(i);
    for (uint32_t index=0;index<3;++index) {
        REX_STORE_U32(0x82DE2708,index);
        PPCContext original{}, hooked{}; original.r3.u32=holder; hooked=original;
        __imp__sub_82284FA0(original,base);
        unsigned char expected[96]; std::memcpy(expected,base+holder+index*96,96);
        Check(rex::cvar::SetFlagByName("camera_fov_scale","1"),"Could not set scale");
        sub_82284FA0(hooked,base);
        Check(std::memcmp(expected,base+holder+index*96,96)==0,"Default changed camera snapshot");
        Check(std::memcmp(&original,&hooked,sizeof(original))==0,"Hook changed guest registers");
        Check(rex::cvar::SetFlagByName("camera_fov_scale","1.2"),"Could not set wide scale");
        hooked.r3.u32=holder;sub_82284FA0(hooked,base);
        auto address=holder+index*96+80;
        Check(std::abs(std::bit_cast<float>(REX_LOAD_U32(address))-66.0f)<0.001f,"Wrong widened angle");
        std::memcpy(base+address,expected+80,4);
        Check(std::memcmp(expected,base+holder+index*96,96)==0,"Hook changed other camera state");
        hooked.r3.u32=holder;sub_82284FA0(hooked,base);
        Check(std::abs(std::bit_cast<float>(REX_LOAD_U32(address))-66.0f)<0.001f,"Scale accumulated across frames");
        REX_STORE_U32(camera+6508,18);
        hooked.r3.u32=holder;sub_82284FA0(hooked,base);
        Check(REX_LOAD_U32(address)==std::bit_cast<uint32_t>(55.0f),"Special camera was changed");
        REX_STORE_U32(camera+6508,0);
    }
    Check(rex::cvar::SetFlagByName("camera_fov_scale","1.2"),"Could not set scale");
    REX_STORE_U32(camera+6520,std::bit_cast<uint32_t>(std::numeric_limits<float>::quiet_NaN()));
    PPCContext ctx{};ctx.r3.u32=holder;sub_82284FA0(ctx,base);
    Check(std::isnan(std::bit_cast<float>(REX_LOAD_U32(holder+192+80))),"Invalid guest angle changed");
    StoreFloat(base,camera+6520,55.0f);
    StoreFloat(base,camera+6580,0.0f);
    StoreFloat(base,camera+6584,0.1f);
    REX_STORE_U32(camera,0x1C000);
    StoreFloat(base,0x8206F070,0.01745329238474369f);
    StoreFloat(base,0x8207047C,0.5f);
    StoreFloat(base,0x820708BC,0.75f);
    StoreFloat(base,0x820722CC,1.0f);
    StoreFloat(base,0x820722DC,0.0f);
    StoreFloat(base,0x82072F40,2.0f);
    StoreFloat(base,0x82BAA498,1.7777777910232544f);
    StoreFloat(base,0x82BAE5DC,1.0f);
    StoreFloat(base,0x820ED468,-0.5f);
    StoreFloat(base,0x82070428,0.0001f);
    StoreFloat(base,0x82070DE0,1280.0f);
    StoreFloat(base,0x82070DE4,720.0f);
    for (unsigned i=0;i<16;++i) StoreFloat(base,camera+272+i*4,(i%5==0)?1.0f:0.0f);
    // Compare real cached screen projections with a stock camera whose effective
    // angle equals the widened render snapshot, at several zoom/scale settings.
    for (float zoom : {1.0f,0.6f}) for (double scale : {0.75,1.0,1.2,1.5}) {
        StoreFloat(base,camera+6528,zoom);
        const auto text=std::to_string(scale);
        Check(rex::cvar::SetFlagByName("camera_fov_scale",text),"Could not set projection scale");
        ctx={};ctx.r3.u32=holder;sub_82284FA0(ctx,base);
        const float snapshotAngle=LoadFloat(base,holder+192+80);
        ctx={};ctx.r1.u32=0x1F000;ctx.r3.u32=camera;sub_82296748(ctx,base);
        const double screenTan=tangentInput;
        unsigned char screenCache[64];std::memcpy(screenCache,base+camera+752,64);
        ctx={};ctx.r1.u32=0x1F000;ctx.r3.u32=camera;sub_82295D10(ctx,base);
        Check(tangentInput==screenTan,"Screen projection and frustum used different FOVs");
        unsigned char frustumCache[320];std::memcpy(frustumCache,base+camera+432,320);
        unsigned char frustumCorners[128];std::memcpy(frustumCorners,base+camera+824,128);
        // Bypass scaling by selecting a special camera, then feed that exact
        // effective angle to the unmodified guest calculations.
        REX_STORE_U32(camera+6508,18);
        StoreFloat(base,camera+6520,snapshotAngle);
        StoreFloat(base,camera+6528,1.0f);
        ctx={};ctx.r1.u32=0x1F000;ctx.r3.u32=camera;sub_82296748(ctx,base);
        Check(tangentInput==screenTan,"Render snapshot and screen projection used different FOVs");
        Check(std::memcmp(screenCache,base+camera+752,64)==0,"Widened screen cache differs from guest reference");
        ctx={};ctx.r1.u32=0x1F000;ctx.r3.u32=camera;sub_82295D10(ctx,base);
        Check(std::memcmp(frustumCache,base+camera+432,320)==0,"Widened frustum differs from guest reference");
        Check(std::memcmp(frustumCorners,base+camera+824,128)==0,"Widened frustum corners differ from guest reference");
        // Overhead markers use this actual world-to-screen routine. Increasing
        // depth at the same direction must preserve placement for both caches.
        for (float depth : {10.0f,50.0f,100.0f}) {
            StoreFloat(base,0x1D000,depth*0.2f);StoreFloat(base,0x1D004,depth*0.1f);
            StoreFloat(base,0x1D008,depth);StoreFloat(base,0x1D00C,1.0f);
            ctx={};ctx.r1.u32=0x1F000;ctx.r3.u32=camera;ctx.r4.u32=0x1D100;ctx.r5.u32=0x1D000;
            sub_822969E8(ctx,base);
            const float x=LoadFloat(base,0x1D100),y=LoadFloat(base,0x1D104);
            std::memcpy(base+camera+752,screenCache,64);
            ctx={};ctx.r1.u32=0x1F000;ctx.r3.u32=camera;ctx.r4.u32=0x1D110;ctx.r5.u32=0x1D000;
            sub_822969E8(ctx,base);
            Check(std::abs(x-LoadFloat(base,0x1D110))<0.001f && std::abs(y-LoadFloat(base,0x1D114))<0.001f,
                  "World marker placement differs from rendered camera projection");
        }
        StoreFloat(base,camera+6520,55.0f);StoreFloat(base,camera+6528,zoom);
        REX_STORE_U32(camera+6508,0);
    }
    // Unrelated tangent callers and other player cameras must remain unchanged.
    Check(rex::cvar::SetFlagByName("camera_fov_scale","1.5"),"Could not set guard scale");
    for (uint32_t link : {0x822967B8u,0x82295D6Cu,0x12345678u}) {
        ctx={};ctx.lr=link;ctx.r31.u32=(link==0x12345678u)?camera:0x14000;
        ctx.f1.f64=0.123;ctx.f13.f64=27.5;ctx.f0.f64=0.01745329238474369;
        sub_82AB9998(ctx,base);
        Check(tangentInput==0.123,"Unrelated tangent or camera was changed");
    }
    VirtualFree(base,0,MEM_RELEASE);
    std::puts("PASS: TU0 snapshot, screen projection, world markers and frustum agree; zoom, buffers and camera guards preserved");
}
'''
    with tempfile.TemporaryDirectory(prefix='crackdown-camera-') as directory:
        folder = Path(directory)
        source, exe = folder / 'camera.cpp', folder / 'camera.exe'
        source.write_text(program.replace('GUEST_FUNCTION', '\n'.join(bodies[name] for name in names))
                         .replace('HOOK', hook.as_posix()).replace('ctx={};', 'ctx=NewContext();'))
        subprocess.run([args.cxx, '-std=c++23', '-O2', '-msse4.1', '-DNDEBUG', '-D_DLL', '-D_MT',
                        '-Xclang', '--dependent-lib=msvcrt', '-DSPDLOG_FMT_EXTERNAL', '-DSPDLOG_COMPILED_LIB',
                        '-I', str(args.generated), '-isystem', str(args.sdk / 'include'), str(source),
                        str(args.sdk / 'lib/rexruntime.lib'), str(args.sdk / 'lib/spdlog.lib'),
                        str(args.sdk / 'lib/fmt.lib'), '-o', str(exe), '-fuse-ld=lld-link'], check=True)
        import os
        environment = dict(os.environ)
        environment['PATH'] = str(args.sdk / 'bin') + os.pathsep + environment.get('PATH', '')
        subprocess.run([str(exe)], env=environment, check=True, timeout=15)


if __name__ == '__main__':
    main()

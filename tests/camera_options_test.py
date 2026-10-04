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
    pattern = re.compile(r'DEFINE_REX_FUNC\(sub_82284FA0\) \{.*?\n\}', re.S)
    bodies = [m[0] for p in args.generated.glob('crackdown_recomp.*.cpp')
              for m in pattern.finditer(p.read_text())]
    if len(bodies) != 1:
        raise AssertionError('Expected exactly one TU0 camera snapshot implementation')
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
#undef DEFINE_REX_FUNC
#define DEFINE_REX_FUNC(name) REX_EXTERN(__imp__##name)
GUEST_FUNCTION
#include "HOOK"
int main() {
    auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr,0x84000000ull,MEM_RESERVE,PAGE_READWRITE));
    Check(base,"Guest memory reservation failed");
    Check(VirtualAlloc(base+0x10000,0x10000,MEM_COMMIT,PAGE_READWRITE),"Camera fixture commit failed");
    Check(VirtualAlloc(base+0x82DE0000,0x10000,MEM_COMMIT,PAGE_READWRITE),"Globals fixture commit failed");
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
    VirtualFree(base,0,MEM_RELEASE);
    std::puts("PASS: actual TU0 snapshot, default/register preservation, triple buffers, FOV scaling, special-camera guard");
}
'''
    with tempfile.TemporaryDirectory(prefix='crackdown-camera-') as directory:
        folder = Path(directory)
        source, exe = folder / 'camera.cpp', folder / 'camera.exe'
        source.write_text(program.replace('GUEST_FUNCTION', bodies[0]).replace('HOOK', hook.as_posix()))
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

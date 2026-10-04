"""Exercise the real TU0 vector acos helper at captured audio failure inputs."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--include", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    args = parser.parse_args()
    pattern = re.compile(r"DEFINE_REX_FUNC\(sub_8225F910\) \{.*?\n\}", re.S)
    bodies = [m[0] for p in args.generated.glob("crackdown_recomp.*.cpp")
              for m in pattern.finditer(p.read_text(encoding="utf-8"))]
    if len(bodies) != 1:
        raise AssertionError("Expected one TU0 spatial-audio vector acos helper")
    hook = Path(__file__).resolve().parents[1] / "src/audio_spatial.cpp"
    program = r'''
#include "crackdown_pch.h"
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
static void Check(bool ok, const char* message) {
    if (!ok) { std::fprintf(stderr, "%s\n", message); std::exit(1); }
}
#undef DEFINE_REX_FUNC
#define DEFINE_REX_FUNC(name) REX_EXTERN(__imp__##name)
GUEST_FUNCTION
#include "HOOK"
int main() {
    constexpr size_t size = 0x82100000ull;
    uint8_t* base = static_cast<uint8_t*>(RESERVE_MEMORY);
    Check(base != nullptr, "Could not reserve test guest memory");
    COMMIT_MEMORY;
    // TU0's polynomial coefficients, domain bias and pi constants. The test
    // needs no executable/assets, and executes every instruction of the helper.
    constexpr uint32_t coefficients[] = {
        0xbd6dd42d, 0xbed65553, 0x3e663246, 0x400b1889,
        0x3f1dd7b6, 0x408980bd, 0xbf983f2f, 0xc0d1360e,
        0xbfaf4418, 0xc08f6ad9, 0x3fb58485, 0x40af6ad8,
        0x3f800001, 0x3f800001, 0x3f800001, 0x3f800001
    };
    for (unsigned i=0;i<16;++i) REX_STORE_U32(0x820856E0+i*4,coefficients[i]);
    REX_STORE_U32(0x82084F20,0x40490fdb);
    REX_STORE_U32(0x82084F24,0x40c90fdb);
    REX_STORE_U32(0x82084F28,0x3ea2f983);
    REX_STORE_U32(0x82084F2C,0x3e22f983);
    // Both one-ULP overshoots were captured immediately before NaN gains.
    for (uint32_t bits : {0x3f800001u,0xbf800001u,0x3f800002u,0xbf800002u}) {
        PPCContext original{}, fixed{};
        original.fpscr.csr=0x1f80; // Runtime masks host floating-point exceptions.
        for (unsigned lane=0;lane<4;++lane) original.v1.u32[lane]=bits;
        fixed=original;
        __imp__sub_8225F910(original,base);
        sub_8225F910(fixed,base);
        for (unsigned lane=0;lane<4;++lane) {
            Check(!std::isfinite(original.v1.f32[lane]), "Original did not reproduce captured failure");
            Check(std::isfinite(fixed.v1.f32[lane]), "Boundary overshoot poisoned corrected angle");
            const float expected=(bits&0x80000000u)?3.14159265358979323846f:0.0f;
            Check(std::abs(fixed.v1.f32[lane]-expected)<0.001f, "Incorrect endpoint angle");
        }
    }
    // Preserve the actual guest approximation bit-for-bit in its valid domain.
    // Different inputs per lane also catch unintended shuffling or broadcasting.
    unsigned cases=0;
    for (int i=-10000;i<=10000;++i) {
        PPCContext original{}, fixed{};
        original.fpscr.csr=0x1f80;
        float value=float(i)/10000.0f;
        original.v1.f32[0]=value; original.v1.f32[1]=-value;
        original.v1.f32[2]=value*0.5f; original.v1.f32[3]=value*0.25f;
        fixed=original;
        __imp__sub_8225F910(original,base);
        sub_8225F910(fixed,base);
        for (unsigned lane=0;lane<4;++lane) {
            Check(original.v1.u32[lane]==fixed.v1.u32[lane], "Clamp changed valid guest angle");
            Check(std::isfinite(fixed.v1.f32[lane]), "Valid input produced a nonfinite angle");
        }
        ++cases;
    }
    UNMAP_MEMORY;
    std::printf("PASS: captured NaN failures corrected; %u four-lane guest angles preserved\n",cases);
}
'''
    if sys.platform == "win32":
        program = "#define NOMINMAX\n#include <windows.h>\n" + program
        program = program.replace("RESERVE_MEMORY", "VirtualAlloc(nullptr,size,MEM_RESERVE,PAGE_READWRITE)")
        program = program.replace("COMMIT_MEMORY", "VirtualAlloc(base+0x82080000,0x10000,MEM_COMMIT,PAGE_READWRITE)")
        program = program.replace("UNMAP_MEMORY", "VirtualFree(base,0,MEM_RELEASE)")
    else:
        program = "#include <sys/mman.h>\n" + program
        program = program.replace("RESERVE_MEMORY", "mmap(nullptr,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0)")
        program = program.replace("base != nullptr", "base != reinterpret_cast<uint8_t*>(MAP_FAILED)")
        program = program.replace("COMMIT_MEMORY", "(void)0")
        program = program.replace("UNMAP_MEMORY", "munmap(base,size)")
    with tempfile.TemporaryDirectory(prefix="crackdown-spatial-audio-") as tmp:
        path = Path(tmp)
        cpp = path / "spatial.cpp"
        exe = path / ("spatial.exe" if sys.platform == "win32" else "spatial")
        cpp.write_text(program.replace("GUEST_FUNCTION", bodies[0]).replace("HOOK", hook.as_posix()))
        subprocess.run([args.cxx, "-std=c++23", "-O2", "-msse4.1",
                        "-DSPDLOG_FMT_EXTERNAL", "-DSPDLOG_COMPILED_LIB",
                        "-I", str(args.generated), "-isystem", str(args.include), str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True, timeout=15)


if __name__ == "__main__":
    main()

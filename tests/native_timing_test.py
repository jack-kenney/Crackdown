"""Execute extracted TU0 timer/getter code with deterministic kernel clock data.

Uses the generated indirect-dispatch table and actual guest arithmetic. Only
unselected replay/file clock sources are stubbed; calling one fails the test.
This validates the original timing machinery, not an unlocked FPS hook.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile


FUNCTIONS = (
    "sub_823260C8", "sub_82326038", "sub_82326248", "sub_82326268",
    "sub_82326280", "sub_82326300", "sub_82326328", "sub_82326348",
    "sub_823263D8", "sub_8254C568", "sub_82744558",
    "__savegprlr_26", "__restgprlr_26", "__savegprlr_28", "__restgprlr_28",
)
REPLAY_BOUNDARIES = (
    "sub_825379E0", "sub_82537280", "sub_82537E00", "sub_82537FF8",
    "sub_82537F20", "sub_82536E78", "sub_82536EF0",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    args = parser.parse_args()
    found = {name: [] for name in FUNCTIONS}
    pattern = re.compile(r"DEFINE_REX_FUNC\(([^)]+)\) \{.*?\n\}", re.S)
    for path in args.generated.glob("crackdown_recomp.*.cpp"):
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            if match[1] in found:
                found[match[1]].append(match[0])
    for name, bodies in found.items():
        if len(bodies) != 1:
            raise AssertionError(f"Expected one generated TU0 implementation: {name}")
    program = r'''
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include "crackdown_funcs.h"
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
static void Check(bool value, const char* message) {
    if (!value) { std::fprintf(stderr,"FAIL: %s\n",message); std::exit(1); }
}
static void Near(double actual, double expected, const char* message) {
    Check(std::isfinite(actual) && std::abs(actual-expected)<0.00001,message);
}
#undef DEFINE_REX_FUNC
#define DEFINE_REX_FUNC(name) REX_EXTERN(name)
BOUNDARIES
GUEST_FUNCTIONS
int main() {
    auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,0x86000000ull,MEM_RESERVE,PAGE_READWRITE));
    Check(base,"Guest memory reservation");
    auto Commit=[&](uint32_t address,size_t size=0x10000) {
        Check(VirtualAlloc(base+address,size,MEM_COMMIT,PAGE_READWRITE),"Fixture commit");
    };
    Commit(0x10000,0x30000);
    for (auto address : {0x82000000u,0x82060000u,0x82070000u,0x82090000u,
                         0x820E0000u,0x82BA0000u,0x82C60000u,0x82D90000u,0x82DE0000u}) Commit(address);
    constexpr uint32_t engine=0x10000,kernel=0x12000,outputs=0x14000,vtable=0x82095D5C;
    auto Float=[&](uint32_t address,float value) { REX_STORE_U32(address,std::bit_cast<uint32_t>(value)); };
    auto Bind=[&](uint32_t offset,uint32_t address,PPCFunc* function) {
        REX_STORE_U32(vtable+offset,address);
        const auto slot=uint32_t(REX_IMAGE_BASE+REX_IMAGE_SIZE+(uint64_t(address-REX_CODE_BASE)*2));
        Commit(slot&~0xFFFFu);
        REX_LOOKUP_FUNC(base,address)=function;
    };
    Bind(12,0x823260C8,sub_823260C8);
    Bind(16,0x82326248,sub_82326248);
    Bind(20,0x82326268,sub_82326268);
    Bind(24,0x82326280,sub_82326280);
    Bind(28,0x82326300,sub_82326300);
    Bind(32,0x82326328,sub_82326328);
    Bind(36,0x82326348,sub_82326348);
    REX_STORE_U32(engine,vtable);
    REX_STORE_U32(0x82DE25C0,engine);
    REX_STORE_U32(0x820008C4,kernel);
    REX_STORE_U32(0x82DE3F90,0); // Ordinary live clock: no replay override.
    Float(0x820722D0,0.001f);
    Float(0x82071980,1000.0f);
    Float(0x8206F394,0.001f);
    Float(0x820ED704,1.0f/30.0f);
    Float(0x820ED634,1.0f/60.0f);
    Float(0x82C63578,1.0f);
    Float(0x82BAEC44,0.0f);
    auto Context=[] {
        PPCContext ctx{};ctx.r1.u32=0x3F000;ctx.lr=0x12345678;
        unsigned index=26;
        for (auto* reg : {&ctx.r26,&ctx.r27,&ctx.r28,&ctx.r29,&ctx.r30,&ctx.r31})
            reg->u64=0x1122334400000000ull+index++;
        return ctx;
    };
    auto Reset=[&](uint32_t now,uint32_t minimum,uint32_t maximum,bool fixed=false) {
        REX_STORE_U32(kernel+16,now);
        REX_STORE_U8(engine+13,fixed);
        REX_STORE_U32(engine+40,minimum);REX_STORE_U32(engine+44,maximum);
        REX_STORE_U32(0x82D99128,0);REX_STORE_U32(0x82D9912C,0);
        REX_STORE_U8(0x82DE3FB0,0);
        auto ctx=Context();ctx.r3.u32=engine;sub_82326038(ctx,base);
        Check(REX_LOAD_U32(engine+36)==now,"Initialization anchors committed wall clock");
        Check(REX_LOAD_U32(engine+32)==0 && REX_LOAD_U32(engine+52)==0,"Initialization clears accounting");
        Check(REX_LOAD_U32(engine+40)==minimum && REX_LOAD_U32(engine+44)==maximum,"Initialization preserves limits");
    };
    auto Tick=[&](uint32_t now,uint32_t expected_ms,bool expected_skip,bool expected_clamp) {
        REX_STORE_U32(kernel+16,now);
        std::memset(base+outputs,0xCD,16);
        auto ctx=Context();const auto before=ctx;
        ctx.r3.u32=engine;ctx.r4.u32=outputs;ctx.r5.u32=outputs+1;ctx.r6.u32=outputs+4;
        sub_823263D8(ctx,base);
        Check(REX_LOAD_U8(outputs)==expected_clamp,"Clamp output");
        Check(REX_LOAD_U8(outputs+1)==!expected_skip,"Step allowed output");
        Near(std::bit_cast<float>(REX_LOAD_U32(outputs+4)),expected_ms*0.001,"Timer seconds output");
        Check(REX_LOAD_U32(0x82D99128)==expected_ms,"Published native milliseconds");
        Check(REX_LOAD_U8(0x82DE3FB0)==1,"Native clock publication flag");
        Check(ctx.r1.u32==before.r1.u32 && ctx.lr==before.lr,"Guest stack and LR preservation");
        unsigned index=26;
        for (auto* reg : {&ctx.r26,&ctx.r27,&ctx.r28,&ctx.r29,&ctx.r30,&ctx.r31})
            Check(reg->u64==0x1122334400000000ull+index++,"Saved register preservation");
    };
    auto Getter=[&](double seconds) {
        auto ctx=Context();sub_8254C568(ctx,base);Near(ctx.f1.f64,seconds,"Getter seconds");
    };
    Reset(1000,1,50);
    Tick(1016,16,false,false);Getter(0.016);
    Tick(1033,17,false,false);Getter(0.017);
    Check(REX_LOAD_U32(engine+36)==1033 && REX_LOAD_U32(engine+32)==33,"Normal committed clock and elapsed sum");
    Tick(1133,50,false,true);
    Check(REX_LOAD_U32(engine+36)==1083,"Clamp retains unconsumed wall time");
    Tick(1133,50,false,false);
    Check(REX_LOAD_U32(engine+36)==1133 && REX_LOAD_U32(engine+32)==133,"Residual consumed on next tick");
    Check(REX_LOAD_U32(0x82D9912C)==133,"Published accumulated milliseconds");
    Near(std::bit_cast<float>(REX_LOAD_U32(engine+60)),0.133,"Accumulated engine seconds");
    Tick(1133,0,true,false);Getter(0.0);
    Check(REX_LOAD_U32(engine+36)==1133 && REX_LOAD_U32(0x82D9912C)==133,"Zero tick leaves committed time and sum unchanged");
    Reset(2000,10,50);
    Tick(2009,0,true,false);
    Check(REX_LOAD_U32(engine+36)==2000,"Below-minimum tick retains residual");
    Tick(2010,10,false,false);
    Reset(1000,1,50);
    Tick(3048,48,false,false);
    Check(REX_LOAD_U32(engine+64)==2000 && REX_LOAD_U32(engine+32)==2048,"Long gap discards accounted 2000ms block");
    Check(REX_LOAD_U32(engine+36)==3048 && REX_LOAD_U32(0x82D9912C)==48,"Long gap publishes only final simulation delta");
    Reset(0xFFFFFFF0u,1,50);
    Tick(0x10,32,false,false);
    Check(REX_LOAD_U32(engine+36)==0x10,"Kernel millisecond wraparound");
    Reset(1000,1,100,true);
    Tick(1033,33,false,false);
    Check(REX_LOAD_U32(engine+40)==33 && REX_LOAD_U32(engine+44)==33,"Fixed mode forces 33ms limits");
    Getter(1.0/30.0); // Fixed getter is exactly 1/30, not the timer's 33ms.
    Float(0x82C63578,0.5f);Getter(1.0/60.0);
    REX_STORE_U8(engine+13,0);Getter(0.033); // Native getter uses committed ms independently of scale.
    REX_STORE_U8(0x82DE3FB0,0);Getter(1.0/120.0); // No published clock: scaled 1/60 fallback.
    VirtualFree(base,0,MEM_RELEASE);
    std::puts("PASS: actual TU0 native timer, residual/min/max/skip/zero/wrap/long-gap accounting, fixed/native getter branches");
}
'''
    boundaries = "\n".join(
        f'REX_EXTERN({name}) {{ Check(false,"Unexpected replay clock source {name}"); }}'
        for name in REPLAY_BOUNDARIES
    )
    program = program.replace("BOUNDARIES", boundaries).replace(
        "GUEST_FUNCTIONS", "\n".join(found[name][0] for name in FUNCTIONS)
    )
    with tempfile.TemporaryDirectory(prefix="crackdown-native-timing-") as directory:
        folder = Path(directory)
        source, exe = folder / "timing.cpp", folder / "timing.exe"
        source.write_text(program, encoding="utf-8")
        subprocess.run([
            args.cxx, "-std=c++23", "-O2", "-msse4.1", "-DNDEBUG", "-D_DLL", "-D_MT",
            "-Xclang", "--dependent-lib=msvcrt", "-DSPDLOG_FMT_EXTERNAL", "-DSPDLOG_COMPILED_LIB",
            "-I", str(args.generated), "-isystem", str(args.sdk / "include"), str(source),
            str(args.sdk / "lib/rexruntime.lib"), str(args.sdk / "lib/spdlog.lib"),
            str(args.sdk / "lib/fmt.lib"), "-o", str(exe), "-fuse-ld=lld-link",
        ], check=True)
        environment = dict(os.environ)
        environment["PATH"] = str(args.sdk / "bin") + os.pathsep + environment.get("PATH", "")
        subprocess.run([str(exe)], env=environment, check=True, timeout=15)


if __name__ == "__main__":
    main()

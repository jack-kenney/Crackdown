"""Verify registration and execute the actual TU0 RetVehicleSeatID callback.

The original callback at 0x821A0C70 has a shared null-component tail at
0x821A0C84. Both paths retain the guest's original byte-load semantics.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile


FUNCTIONS = ("sub_821A0C70",)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    args = parser.parse_args()

    mappings = (args.generated / "crackdown_init.cpp").read_text(encoding="utf-8")
    for address, function in (("821A0C70", FUNCTIONS[0]),):
        entries = re.findall(
            rf"\{{\s*0x{address}\s*,\s*([A-Za-z_][A-Za-z_0-9]*)\s*\}}",
            mappings, re.I,
        )
        if entries != [function]:
            raise AssertionError(f"Expected unique guest mapping 0x{address} -> {function}, got {entries}")
    registration = (args.generated / "crackdown_register.cpp").read_text(encoding="utf-8")
    if not re.search(r"SetFunction\(\s*0x821A0C70\s*,\s*sub_821A0C70\s*\)", registration):
        raise AssertionError("RetVehicleSeatID callback missing from generated registrar")

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
#include <cstdio>
#include <cstdlib>
static unsigned checks;
static void Check(bool value,const char* message) {
    ++checks;
    if (!value) { std::fprintf(stderr,"FAIL: %s\n",message);std::exit(1); }
}
#undef DEFINE_REX_FUNC
#define DEFINE_REX_FUNC(name) REX_EXTERN(name)
GUEST_FUNCTIONS
int main() {
    auto* base=static_cast<uint8_t*>(VirtualAlloc(nullptr,0x86000000ull,MEM_RESERVE,PAGE_READWRITE));
    Check(base,"Guest address space reservation");
    auto Commit=[&](uint32_t address) {
        Check(VirtualAlloc(base+(address&~0xffffu),0x10000,MEM_COMMIT,PAGE_READWRITE),"Guest fixture page commit");
    };
    Commit(0);Commit(0x10000);Commit(0x20000);
    constexpr uint32_t receiver=0x10000,component=0x20000;
    auto Bind=[&](uint32_t address,PPCFunc* function) {
        const auto slot=uint32_t(REX_IMAGE_BASE+REX_IMAGE_SIZE+uint64_t(address-REX_CODE_BASE)*2);
        Commit(slot);
        REX_LOOKUP_FUNC(base,address)=function;
    };
    Bind(0x821A0C70,sub_821A0C70);
    Check(REX_LOOKUP_FUNC(base,0x821A0C70)==sub_821A0C70,"Callback dispatch uses its own registered entry");
    auto Call=[&](uint32_t entry,unsigned expected) {
        PPCContext ctx{};
        ctx.r3.u64=0x1122334400000000ull|receiver;
        ctx.r1.u32=0x1f000;
        ctx.r4.u64=0xaabbccddeeff0011ull;
        ctx.r29.u64=0x123456789abcdef0ull;
        ctx.lr=0x89abcdef;
        auto* function=REX_LOOKUP_FUNC(base,entry);
        Check(function!=nullptr,"Registered callback is callable");
        function(ctx,base);
        Check(ctx.r3.u64==expected,"Original callback returns expected unsigned byte");
        Check(ctx.r1.u32==0x1f000 && ctx.lr==0x89abcdef &&
              ctx.r4.u64==0xaabbccddeeff0011ull && ctx.r29.u64==0x123456789abcdef0ull,
              "Leaf callback preserves stack, return address and unrelated registers");
    };
    REX_STORE_U32(receiver+292,component);
    REX_STORE_U8(component+5438,7);REX_STORE_U8(component+5470,227);REX_STORE_U8(5470,91);
    Call(0x821A0C70,7);
    REX_STORE_U8(component+5438,254);Call(0x821A0C70,254);
    REX_STORE_U8(component+5438,0);Call(0x821A0C70,0);
    REX_STORE_U32(receiver+292,0);
    // Codegen merges the original C84 tail into the correct callback entry.
    Call(0x821A0C70,91);
    REX_STORE_U8(5470,255);Call(0x821A0C70,255);
    REX_STORE_U8(5470,0);Call(0x821A0C70,0);
    VirtualFree(base,0,MEM_RELEASE);
    std::printf("PASS: actual RetVehicleSeatID registration, nonnull byte and shared null tail (%u checks)\n",checks);
}
'''
    program = program.replace("GUEST_FUNCTIONS", "\n".join(found[name][0] for name in FUNCTIONS))
    with tempfile.TemporaryDirectory(prefix="crackdown-function-registration-") as directory:
        folder = Path(directory)
        source, exe = folder / "registration.cpp", folder / "registration.exe"
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

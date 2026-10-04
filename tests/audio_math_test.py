"""Execute the generated Bink initializer and check normalization/allocations."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from fix_audio_math import CORRECTION, FUNCTION, fix_directory, fix_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--include", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    args = parser.parse_args()
    functions = [m[0] for p in args.generated.glob("crackdown_recomp.*.cpp")
                 for m in FUNCTION.finditer(p.read_text(encoding="utf-8"))]
    if len(functions) != 1 or functions[0].count(CORRECTION) != 2:
        raise AssertionError("Expected one corrected TU0 Bink initializer")
    body = functions[0]
    original = body.replace(CORRECTION,
        "\tctx.f13.u64 = uint64_t(rex::ppu_frsqrte_lut.data[ctx.f0.u64 >> 49]) << 32;")
    if fix_source(original) != (body, 2) or fix_source(body) != (body, 0):
        raise AssertionError("Correction must patch twice and be idempotent")
    unrelated = original.replace("__imp__sub_82B6D428", "__imp__sub_82100000")
    if fix_source(unrelated) != (unrelated, 0):
        raise AssertionError("Unrelated guest instructions must remain unchanged")
    invalid = original.replace("ctx.f0.u64 >> 49", "ctx.f0.u64 >> 48", 1)
    with tempfile.TemporaryDirectory(prefix="crackdown-audio-math-") as tmp:
        path = Path(tmp)
        source = path / "crackdown_recomp.0.cpp"
        for newline in ("\n", "\r\n"):
            source.write_bytes(original.replace("\n", newline).encode())
            if fix_directory(path) != (2, 1) or fix_directory(path) != (0, 0):
                raise AssertionError("Directory correction must be idempotent")
            if newline == "\r\n" and b"\n" in source.read_bytes().replace(b"\r\n", b""):
                raise AssertionError("CRLF must be preserved")
        source.write_text(original)
        (path / "crackdown_recomp.1.cpp").write_text(invalid)
        before = source.read_bytes()
        try:
            fix_directory(path)
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown emitter output must fail")
        if source.read_bytes() != before:
            raise AssertionError("Failed validation must leave sources untouched")

        # This is the real generated initializer, with only heap and ABI helpers
        # supplied by the test. Check both DCT and RDFT, mono/stereo, all block sizes.
        program = r'''
#include <rex/ppc/context.h>
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>
static uint32_t next_address;
static std::vector<uint32_t> allocations;
static void Check(bool ok, const char* message) {
    if (!ok) { std::fprintf(stderr, "%s\n", message); std::exit(1); }
}
static uint32_t Allocate(uint32_t size) {
    Check(size > 0 && size < 65536, "Bink requested an invalid scratch allocation");
    allocations.push_back(size);
    uint32_t address = next_address;
    next_address += (size + 31) & ~31u;
    return address;
}
PPC_FUNC_IMPL(__savegprlr_22) {}
PPC_FUNC_IMPL(__restgprlr_22) {}
PPC_FUNC_IMPL(sub_82B6BBE8) {
    const uint32_t address = Allocate(ctx.r4.u32);
    PPC_STORE_U32(ctx.r3.u32, address);
    ctx.r3.u32 = address;
}
PPC_FUNC_IMPL(sub_82B6BCE0) { ctx.r3.u32 = Allocate(ctx.r3.u32); }
INITIALIZER
int main() {
    // Eight bytes of constants at the game's high addresses are mapped by a
    // sparse OS allocation; no real game image or assets are required.
    constexpr size_t size = 0x82100000ull;
    uint8_t* base = static_cast<uint8_t*>(RESERVE_MEMORY);
    Check(base != nullptr, "Could not reserve test guest memory");
    PPC_STORE_U32(0x82000A8C, std::bit_cast<uint32_t>(2.0f));
    PPC_STORE_U32(0x82000A94, 0);
    PPC_STORE_U64(0x820ED868, std::bit_cast<uint64_t>(0.5));
    PPC_STORE_U64(0x820ED710, std::bit_cast<uint64_t>(3.0));
    for (unsigned i=0; i<26; ++i) PPC_STORE_U32(0x835DD078+i*4, 1000000);
    unsigned cases=0;
    for (unsigned rate : {16000u,22050u,44100u,48000u})
    for (unsigned channels : {1u,2u})
    for (unsigned dct : {0u,1u}) {
        next_address=0x40000;
        allocations.clear();
        PPCContext ctx{};
        ctx.r1.u32=0x20000;
        ctx.r3.u32=rate;
        ctx.r4.u32=channels;
        ctx.r5.u32=dct;
        __imp__sub_82B6D428(ctx,base);
        const uint32_t decoder=ctx.r3.u32;
        const unsigned block=(rate>=44100?2048:rate>=22050?1024:512)*(dct?1:channels);
        PPCRegister gain{}; gain.u32=PPC_LOAD_U32(decoder+4);
        const float expected=2.0f/std::sqrt(float(block));
        Check(std::isfinite(gain.f32) && gain.f32>0 &&
              std::abs(gain.f32-expected)<expected*0.00001f,
              "Bink normalization is zero, nonfinite or incorrect");
        Check(PPC_LOAD_U32(decoder)==block, "Incorrect transform block size");
        Check(allocations.size()==6 && allocations[1]==(unsigned(std::sqrt(float(block/2)))+2)*4,
              "Incorrect square-root scratch allocation");
        Check(ctx.r1.u32==0x20000, "Initializer changed the guest stack");
        ++cases;
    }
    UNMAP_MEMORY;
    std::printf("PASS: %u generated Bink initializers, normalization and scratch allocations\n",cases);
}
'''
        # Memory reserve is larger than the guest constants, with pages committed
        # only where the initializer touches them.
        program = program.replace("0x82100000ull", "0x83600000ull")
        if sys.platform == "win32":
            program = "#define NOMINMAX\n#include <windows.h>\n" + program
            program = program.replace("RESERVE_MEMORY", "VirtualAlloc(nullptr,size,MEM_RESERVE,PAGE_READWRITE)")
            program = program.replace("    PPC_STORE_U32(0x82000A8C", "    VirtualAlloc(base,0x80000,MEM_COMMIT,PAGE_READWRITE);\n    VirtualAlloc(base+0x82000000,0x100000,MEM_COMMIT,PAGE_READWRITE);\n    VirtualAlloc(base+0x835D0000,0x10000,MEM_COMMIT,PAGE_READWRITE);\n    PPC_STORE_U32(0x82000A8C")
            program = program.replace("UNMAP_MEMORY", "VirtualFree(base,0,MEM_RELEASE)")
        else:
            program = "#include <sys/mman.h>\n" + program
            program = program.replace("RESERVE_MEMORY", "mmap(nullptr,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0)")
            program = program.replace("base != nullptr", "base != reinterpret_cast<uint8_t*>(MAP_FAILED)")
            program = program.replace("UNMAP_MEMORY", "munmap(base,size)")
        cpp, exe = path / "math.cpp", path / ("math.exe" if sys.platform=="win32" else "math")
        cpp.write_text(program.replace("INITIALIZER",body))
        subprocess.run([args.cxx,"-std=c++23","-O2","-msse4.1",
                        "-DSPDLOG_FMT_EXTERNAL","-DSPDLOG_COMPILED_LIB",
                        "-isystem",str(args.include),str(cpp),"-o",str(exe)],check=True)
        subprocess.run([str(exe)],check=True,timeout=15)


if __name__ == "__main__":
    main()

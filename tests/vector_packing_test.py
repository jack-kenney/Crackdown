"""Compile generated FLOAT16_4 packs and verify exact finite half values."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from fix_vector_packing import PACK, fix_directory, fix_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--cxx", required=True)
    args = parser.parse_args()
    examples = {}
    for path in sorted(args.generated.glob("crackdown_recomp.*.cpp")):
        source = path.read_text(encoding="utf-8")
        for match in PACK.finditer(source):
            if "const auto pack_source =" not in match["body"]:
                raise AssertionError("Generated input snapshot missing in " + str(path))
            immediates = tuple(map(int, match["comment"].strip().split(",")[-2:]))
            if immediates[0] == 2:
                examples.setdefault(immediates, match[0])
    if not examples:
        raise AssertionError("No generated aliased FLOAT16_4 instructions found")

    bodies = []
    for (mask, shift), block in sorted(examples.items()):
        original = re.sub(r"\t\{\n\tconst auto pack_source = ctx\.v\d+;\n", "", block)
        register = re.search(r"vpkd3d128 v(\d+),", original)[1]
        original = original.replace("pack_source.u32[", "ctx.v" + register + ".u32[")
        if original.endswith("\t}\n"):
            original = original[:-3]
        fixed, count = fix_source(original)
        if fixed != block:
            raise AssertionError("Generated instruction differs from the validated workaround")
        if count != 1 or fix_source(fixed) != (fixed, 0):
            raise AssertionError("Workaround must patch once and be idempotent")
        nonaliased = original.replace("v{0},v{0},5".format(register), "v999,v" + register + ",5")
        if fix_source(nonaliased) != (nonaliased, 0):
            raise AssertionError("Nonaliased packs must remain unchanged")
        try:
            fix_source(original.replace("ctx.v" + register + ".u32[", "unknown.u32[", 1))
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown emitter output must fail")
        normalized = fixed.replace("ctx.v" + register + ".", "ctx.v0.")
        bodies.append((shift, normalized))

    program = r'''
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
union V { uint8_t u8[16]; uint16_t u16[8]; uint32_t u32[4]; float f32[4]; };
union R { uint32_t u32; uint16_t u16; float f32; };
struct Context { V v0; };
FUNCTIONS
float decode_half(uint16_t h) {
    const unsigned exponent = (h >> 10) & 31;
    const float fraction = float(h & 1023) / 1024.0f;
    const float magnitude = std::ldexp(exponent ? 1.0f + fraction : fraction,
                                      exponent ? int(exponent) - 15 : -14);
    return std::copysign(magnitude, h & 0x8000 ? -1.0f : 1.0f);
}
int main() {
    unsigned tested = 0;
    for (const auto& pack : std::array<Pack, PACK_COUNT>{{PACKS}}) {
        for (unsigned h = 0; h < 65536; ++h) {
            if ((h & 0x7C00) == 0x7C00) continue;
            for (unsigned lane = 0; lane < 4; ++lane) {
                uint16_t values[4] = {0xBC00, 0x8000, 0x3C00, 0xB400};
                values[lane] = uint16_t(h);
                Context input{};
                for (unsigned j = 0; j < 4; ++j)
                    input.v0.u32[3-j] = std::bit_cast<uint32_t>(decode_half(values[j]));
                Context expected = input;
                for (unsigned j = 0; j < 4; ++j)
                    expected.v0.u16[3-j+2*pack.shift] = values[j];
                pack.function(input);
                if (std::memcmp(&input, &expected, sizeof(input))) {
                    std::printf("FAIL half=%04X lane=%u shift=%u\n", h, lane, pack.shift);
                    return 1;
                }
                ++tested;
            }
        }
    }
    std::printf("PASS: %u generated pack vectors, including preserved lanes\n", tested);
}
'''
    functions = []
    for i, (_, body) in enumerate(bodies):
        functions.append("void pack_{}(Context& ctx) {{ R temp{{}}; V vTemp{{}};\n{}\n}}".format(i, body))
    functions.append("struct Pack { void (*function)(Context&); unsigned shift; };")
    program = (program.replace("FUNCTIONS", "\n".join(functions))
               .replace("PACK_COUNT", str(len(bodies)))
               .replace("PACKS", ",".join("{pack_%d,%d}" % (i, shift) for i, (shift, _) in enumerate(bodies))))
    with tempfile.TemporaryDirectory(prefix="crackdown-pack-test-") as tmp:
        path = Path(tmp)
        cpp, exe = path / "pack.cpp", path / ("pack.exe" if sys.platform == "win32" else "pack")
        cpp.write_text(program, encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++23", "-O2", str(cpp), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    with tempfile.TemporaryDirectory(prefix="crackdown-pack-rewrite-") as tmp:
        path = Path(tmp)
        example = next(iter(examples.values()))
        unpatched = re.sub(r"\t\{\n\tconst auto pack_source = ctx\.v\d+;\n", "", example)
        reg = re.search(r"vpkd3d128 v(\d+),", unpatched)[1]
        unpatched = unpatched.replace("pack_source.u32[", "ctx.v" + reg + ".u32[")
        if unpatched.endswith("\t}\n"):
            unpatched = unpatched[:-3]
        for newline in ("\n", "\r\n"):
            target = path / "crackdown_recomp.0.cpp"
            target.write_bytes(unpatched.replace("\n", newline).encode("utf-8"))
            if fix_directory(path) != (1, 1) or fix_directory(path) != (0, 0):
                raise AssertionError("Directory rewrite must be idempotent")
            if newline == "\r\n" and b"\n" in target.read_bytes().replace(b"\r\n", b""):
                raise AssertionError("CRLF must be preserved")
        valid = path / "crackdown_recomp.0.cpp"
        invalid = path / "crackdown_recomp.1.cpp"
        valid.write_text(unpatched, encoding="utf-8")
        invalid.write_text(unpatched.replace("ctx.v" + reg + ".u32[", "unknown.u32[", 1), encoding="utf-8")
        before = valid.read_bytes()
        try:
            fix_directory(path)
        except ValueError:
            pass
        else:
            raise AssertionError("Unknown emission in a directory must fail")
        if valid.read_bytes() != before:
            raise AssertionError("Validation failure must leave all sources untouched")


if __name__ == "__main__":
    main()

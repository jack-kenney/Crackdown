"""Correct TU0 Bink audio normalization in ReXGlue 0.2.2 generated code.

The SDK's frsqrte table mishandles the packed double exponent: 2048 yields
about 8e141 instead of 1/sqrt(2048). The guest's Newton iterations overflow,
making the decoder gain zero and its scratch allocation size invalid.
Use a host reciprocal square root only in the two Bink initialization sites.
"""
import argparse
from pathlib import Path
import re

FUNCTION = re.compile(r"PPC_FUNC_IMPL\(__imp__sub_82B6D428\) \{.*?\n\}", re.S)
ESTIMATE = re.compile(
    r"(?P<comment>\t// frsqrte f13,f0\n)"
    r"\tctx\.f13\.u64 = uint64_t\(rex::ppu_frsqrte_lut\.data\[ctx\.f0\.u64 >> 49\]\) << 32;"
)
CORRECTION = "\tctx.f13.f64 = 1.0 / std::sqrt(ctx.f0.f64); // Bink normalization"


def fix_source(source):
    count = 0

    def replace_function(match):
        nonlocal count
        body = match[0]
        original = len(ESTIMATE.findall(body))
        fixed = body.count(CORRECTION)
        if original + fixed != 2:
            raise ValueError("Unexpected Bink normalization code; review the SDK workaround")
        count += original
        return ESTIMATE.sub(lambda m: m["comment"] + CORRECTION, body)

    return FUNCTION.sub(replace_function, source), count


def fix_directory(directory):
    updates = []
    total = 0
    for path in sorted(directory.glob("crackdown_recomp.*.cpp")):
        original = path.read_bytes().decode("utf-8")
        newline = "\r\n" if "\r\n" in original else "\n"
        fixed, count = fix_source(original.replace("\r\n", "\n"))
        if count:
            updates.append((path, fixed.replace("\n", newline).encode("utf-8")))
            total += count
    for path, contents in updates:
        path.write_bytes(contents)
    return total, len(updates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generated", type=Path)
    args = parser.parse_args()
    if not args.generated.is_dir():
        parser.error("Generated source directory does not exist; run codegen first")
    count, files = fix_directory(args.generated)
    print(f"Bink normalization: {count} instructions in {files} files")


if __name__ == "__main__":
    main()

"""Snapshot aliased FLOAT16_4 inputs in ReXGlue 0.10.0 generated code.

Based on BChapmanDev's workaround in https://github.com/SkiddyToast/Crackdown/pull/1.
"""
import argparse
from pathlib import Path
import re

PACK = re.compile(
    r"(?P<comment>\t// vpkd3d128 v(?P<reg>\d+),v(?P=reg),5,\d+,\d+\n)"
    r"(?P<body>(?:\t(?!//)[^\n]*\n)+)"
)


def fix_source(source):
    changed = 0

    def replace(match):
        nonlocal changed
        body = match["body"]
        if "const auto pack_source =" in body:
            return match[0]
        register = "ctx.v" + match["reg"]
        if body.count(register + ".u16[") != 8 or body.count(register + ".u32[") != 16:
            raise ValueError("Unexpected FLOAT16_4 emitter output; review the SDK workaround")
        # Source reads use u32; destination writes use u16. Keep every emitted
        # conversion and destination lane intact, while reading a snapshot.
        body = body.replace(register + ".u32[", "pack_source.u32[")
        changed += 1
        return (match["comment"] + "\t{\n\tconst auto pack_source = "
                + register + ";\n" + body + "\t}\n")

    return PACK.sub(replace, source), changed


def fix_directory(directory):
    # Validate all files before writing any: an unknown emitter must not leave
    # a partially rewritten tree.
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
    print("FLOAT16_4 input snapshots: {} instructions in {} files".format(count, files))


if __name__ == "__main__":
    main()

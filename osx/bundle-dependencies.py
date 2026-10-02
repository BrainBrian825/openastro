#!/usr/bin/env python3
"""Bundle and check the Homebrew libraries used by a macOS app."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def macho_files(bundle):
    # Include frameworks and Qt plugins, which macdeployqt places below Contents.
    for path in (bundle / "Contents").rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            magic = handle.read(4)
        if magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            yield path


def linked_libraries(binary):
    lines = run("otool", "-L", str(binary)).splitlines()[1:]
    return [line.strip().split(" (", 1)[0] for line in lines]


def install_id(binary):
    lines = run("otool", "-D", str(binary)).splitlines()
    return lines[1].strip() if len(lines) > 1 else None


def source_library(homebrew, name):
    candidates = [homebrew / "lib" / name]
    candidates.extend((homebrew / "opt").glob(f"*/lib/{name}"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"Cannot find Homebrew library {name}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: bundle-dependencies.py oaCapture.app")
    bundle = Path(sys.argv[1]).resolve()
    contents = bundle / "Contents"
    frameworks = contents / "Frameworks"
    executable = contents / "MacOS" / "oaCapture"
    homebrew = Path(run("brew", "--prefix"))

    # A dependency of a bundled library may still use @rpath. Give dyld an
    # app-local location to search, including when Gatekeeper translocates it.
    rpaths = run("otool", "-l", str(executable))
    if "path @executable_path/../Frameworks " not in rpaths:
        subprocess.check_call(
            ["install_name_tool", "-add_rpath", "@executable_path/../Frameworks", str(executable)]
        )

    while True:
        copied = False
        for binary in list(macho_files(bundle)):
            own_id = install_id(binary)
            for library in linked_libraries(binary):
                if library == own_id or library.startswith(("/usr/lib/", "/System/Library/")):
                    continue

                if library.startswith("@rpath/"):
                    target = frameworks / library.removeprefix("@rpath/")
                elif library.startswith("@executable_path/"):
                    target = executable.parent / library.removeprefix("@executable_path/")
                elif library.startswith("@loader_path/"):
                    target = binary.parent / library.removeprefix("@loader_path/")
                elif library.startswith(str(homebrew) + "/"):
                    target = frameworks / Path(library).name
                    subprocess.check_call(
                        ["install_name_tool", "-change", library,
                         "@rpath/" + target.name, str(binary)]
                    )
                else:
                    raise RuntimeError(f"Unexpected dependency in {binary}: {library}")

                if target.exists():
                    continue
                if target.parent != frameworks or not target.name.endswith(".dylib"):
                    raise RuntimeError(f"Missing bundled dependency in {binary}: {library}")
                source = source_library(homebrew, target.name)
                shutil.copy2(source, target)
                os.chmod(target, 0o755)
                print(f"Bundled {target.name} for {binary.relative_to(bundle)}")
                copied = True
        if not copied:
            break

    binaries = list(macho_files(bundle))
    for binary in binaries:
        if "arm64" not in run("lipo", "-archs", str(binary)).split():
            raise RuntimeError(f"No Apple silicon binary in {binary}")
    print(f"Verified dependencies and arm64 slices for {len(binaries)} Mach-O files")


if __name__ == "__main__":
    main()

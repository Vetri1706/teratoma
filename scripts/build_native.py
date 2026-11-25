#!/usr/bin/env python3
"""Build native queues, threaded stress checks, and an in-process CPython daemon."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
ROOT = Path(__file__).resolve().parents[1]


def prerequisites():
    if sys.platform not in ("linux", "darwin"):
        raise SystemExit("Native daemons require Linux or macOS POSIX APIs; on Windows, use Linux through WSL or run scripts/test_portable.py.")
    try:
        compiler = shlex.split(os.environ.get("CC", "clang"))
    except ValueError as error:
        raise SystemExit(f"Invalid CC command: {error}. Set CC to a C11 compiler, for example CC=clang or CC=gcc.") from None
    if not compiler or shutil.which(compiler[0]) is None:
        raise SystemExit("A C11 compiler is required. Install Clang, or set CC to an available compiler (for example CC=gcc).")
    include_path = sysconfig.get_path("include")
    library_directory = sysconfig.get_config_var(
        "PYTHONFRAMEWORKPREFIX" if sysconfig.get_config_var("PYTHONFRAMEWORK") else "LIBDIR")
    library_name = sysconfig.get_config_var("LDLIBRARY")
    if not include_path or not library_directory or not library_name:
        raise SystemExit(f"CPython embedding configuration is missing. Install the development headers and library matching {sys.executable}, then retry with that interpreter.")
    include = Path(include_path)
    library = Path(library_directory) / library_name
    missing = [str(path) for path in (include / "Python.h", library) if not path.is_file()]
    if missing:
        raise SystemExit(f"CPython embedding files are missing: {', '.join(missing)}. Install the development headers and library matching {sys.executable}.")
    if shutil.which("file") is None:
        raise SystemExit("The 'file' utility is required to inspect the native binaries. Install your platform's file package and retry.")
    if sys.platform == "darwin" and shutil.which("otool") is None:
        raise SystemExit("The 'otool' utility is required on macOS. Install the Xcode Command Line Tools with xcode-select --install.")
    return compiler, include, library


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sanitizers", action="store_true")
    parser.add_argument("--skip-stress", action="store_true")
    options = parser.parse_args(argv)
    compiler, include, library = prerequisites()
    output = ROOT / "bin"
    output.mkdir(exist_ok=True)
    report = {"python": sys.version, "python_executable": sys.executable, "commands": [], "sanitizers": {}}

    def execute(command, required=True):
        print(shlex.join(str(item) for item in command), flush=True)
        result = subprocess.run([str(item) for item in command], cwd=ROOT, capture_output=True, text=True, timeout=120)
        report["commands"].append({"argv": [str(item) for item in command], "returncode": result.returncode,
                                    "stdout": result.stdout, "stderr": result.stderr})
        if result.stdout:
            print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
        if required and result.returncode:
            raise RuntimeError(f"command failed with status {result.returncode}: {command[0]}")
        return result

    try:
        execute(compiler + ["--version"])
        flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"]
        if sys.platform != "darwin":
            flags.append("-D_XOPEN_SOURCE=700")
        queue, stress = ROOT / "native/aegis_queue.c", ROOT / "native/queue_stress.c"
        extension = ".dylib" if sys.platform == "darwin" else ".so"
        library_output = output / ("libaegis_native" + extension)
        shared_flags = ["-dynamiclib", "-Wl,-install_name,@rpath/libaegis_native.dylib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
        execute(compiler + flags + shared_flags + [queue, "-o", library_output])
        stress_output = output / "aegis_queue_stress"
        execute(compiler + flags + ["-pthread", queue, stress, "-o", stress_output])
        report["python_library"] = str(library)
        libraries = shlex.split((sysconfig.get_config_var("LIBS") or "") + " " + (sysconfig.get_config_var("SYSLIBS") or ""))
        daemon = output / "aegisdbd"
        execute(compiler + flags + ["-I" + str(include), "-DAEGIS_PYTHON_HOME=" + json.dumps(sys.base_prefix),
                ROOT / "native/aegisdbd.c", library, "-Wl,-rpath," + str(library.parent)] + libraries + ["-o", daemon])
        report["binary_identity"] = execute(["file", daemon, library_output]).stdout.strip()
        if sys.platform == "darwin":
            report["linked_libraries"] = execute(["otool", "-L", daemon]).stdout
        if not options.skip_stress:
            report["stress"] = json.loads(execute([stress_output, "100000"]).stdout)
        if options.sanitizers:
            for name, sanitizer in (("address_undefined", "address,undefined"), ("thread", "thread")):
                executable = output / ("aegis_queue_stress_" + name)
                compiled = execute(compiler + ["-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                    "-fno-omit-frame-pointer", "-fsanitize=" + sanitizer, "-pthread", queue, stress, "-o", executable], required=False)
                if compiled.returncode:
                    report["sanitizers"][name] = {"status": "compiler_unavailable", "detail": compiled.stderr}
                    continue
                result = execute([executable, "100000"], required=False)
                if result.returncode:
                    unsupported = ("unexpected memory mapping", "ThreadSanitizer is not supported", "failed to mmap")
                    if any(marker in result.stderr for marker in unsupported):
                        report["sanitizers"][name] = {"status": "runtime_unavailable", "detail": result.stderr}
                        continue
                    report["sanitizers"][name] = {"status": "failed", "detail": result.stderr}
                    raise RuntimeError(f"{name} sanitizer stress failed")
                report["sanitizers"][name] = {"status": "passed", "stress": json.loads(result.stdout)}
        report["status"] = "passed"
    except Exception:
        report["status"] = "failed"
        raise
    finally:
        (ROOT / "native/build_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"daemon": str(daemon), "queue_library": str(library_output), "report": str(ROOT / "native/build_report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

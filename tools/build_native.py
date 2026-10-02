"""用 zig 本地编译 native/flowcore.c(本机没有 MSVC)。

用法: python tools/build_native.py
产物: native/flowcore.pyd(仅 Windows x86_64, 只用于在 PC 上跑等价测试/像素闸门;
     设备上要的 aarch64/armv7 .so 由 CI 的 p4a recipe 编)。
"""
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "p4a-recipes" / "flowcore" / "src" / "flowcore.c"
OUT = ROOT / "native" / "flowcore.pyd"


def main():
    include = sysconfig.get_paths()["include"]
    base = sysconfig.get_config_var("installed_base")
    cmd = [sys.executable, "-m", "ziglang", "cc", "-O2", "-shared",
           "-o", str(OUT), str(SRC), f"-I{include}", f"-L{base}/libs", "-lpython311"]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print("built:", OUT)


if __name__ == "__main__":
    main()

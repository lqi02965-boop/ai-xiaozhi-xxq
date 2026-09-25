"""idf.py 包装器：在 Git Bash 环境下可靠运行 ESP-IDF 命令并回收输出。

用法：
    python scripts/idf_run.py --version
    python scripts/idf_run.py set-target esp32s3
    python scripts/idf_run.py build
"""
import os
import subprocess
import sys

IDF_PATH = r"D:/esp/v6.1/esp-idf"
IDF_TOOLS_PATH = r"D:/Espressif/tools"
IDF_PYTHON_ENV_PATH = r"D:/Espressif/tools/python/v6.1/venv"

TOOL_BIN_DIRS = [
    r"D:/Espressif/tools/xtensa-esp-elf/esp-15.2.0_20251204/xtensa-esp-elf/bin",
    r"D:/Espressif/tools/riscv32-esp-elf/esp-15.2.0_20251204/riscv32-esp-elf/bin",
    r"D:/Espressif/tools/riscv32-esp-elf-gdb/*/riscv32-esp-elf-gdb/bin",
    r"D:/Espressif/tools/xtensa-esp-elf-gdb/*/xtensa-esp-elf-gdb/bin",
    r"D:/Espressif/tools/ninja/1.12.1",
    r"D:/Espressif/tools/cmake/4.0.3/bin",
    r"D:/Espressif/tools/ccache/*/ccache/bin",
]


def build_env():
    env = os.environ.copy()
    # Git Bash 的 MSYSTEM 会让 idf.py 拒绝运行
    env.pop("MSYSTEM", None)
    env["IDF_PATH"] = IDF_PATH
    env["IDF_TOOLS_PATH"] = IDF_TOOLS_PATH
    env["IDF_PYTHON_ENV_PATH"] = IDF_PYTHON_ENV_PATH
    # VS Code 扩展会设此变量；命令行下不设会导致 idf_component_manager 崩溃
    env.setdefault("ESP_IDF_VERSION", "6.1")
    import glob

    extra = []
    for pattern in TOOL_BIN_DIRS:
        extra.extend(sorted(glob.glob(pattern)))
    env["PATH"] = ";".join(extra) + ";" + env["PATH"]
    return env


def main():
    idf_python = os.path.join(IDF_PYTHON_ENV_PATH, "Scripts", "python.exe")
    cmd = [idf_python, os.path.join(IDF_PATH, "tools", "idf.py")] + sys.argv[1:]
    p = subprocess.run(cmd, env=build_env(), capture_output=True, text=True,
                       errors="replace", cwd=os.getcwd())
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())

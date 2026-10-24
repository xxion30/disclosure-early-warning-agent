"""Compatibility entry point: all user launches now use the Docker demo stack.

Prefer .\\start.ps1 (Windows) or sh ./start.sh (macOS/Linux).
Python/uv are unnecessary for those entry points. Configure ports in .env.
"""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if len(sys.argv) != 1:
        raise SystemExit('실행 모드는 하나입니다. --fake/--demo/포트 옵션 대신 .env 설정과 시작 스크립트를 사용하세요.')
    root = Path(__file__).resolve().parents[2]
    command = (['powershell.exe', '-NoProfile', '-File', str(root / 'start.ps1')]
               if os.name == 'nt' else ['sh', str(root / 'start.sh')])
    raise SystemExit(subprocess.call(command, cwd=root))


if __name__ == '__main__':
    main()

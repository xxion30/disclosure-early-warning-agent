"""Phase 1 offline verification. Never starts paid models or modifies the user's DB."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-tests', action='store_true')
    args = parser.parse_args()
    commands = [] if args.skip_tests else [[sys.executable, '-m', 'pytest', '-q']]
    commands.append([sys.executable, '-m', 'compileall', '-q', 'shared', 'risk_engine', 'api', 'mcp_servers/business', 'agent/adapters', 'migrations'])
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
    print('R4 checks passed. R2 graph / R3 collector / R5 UI integration requires separate validation.')


if __name__ == '__main__':
    main()

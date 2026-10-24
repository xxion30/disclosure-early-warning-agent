"""Share one session JWT across services, without putting it in an image or .env."""
import os
from pathlib import Path
import sys


def main():
    secret = Path('/run/dartch-demo/jwt_secret').read_text().strip()
    if len(secret) < 32:
        raise SystemExit('시연 세션 준비가 필요합니다. 시작 스크립트를 실행하세요.')
    os.environ['JWT_SECRET'] = secret
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    main()

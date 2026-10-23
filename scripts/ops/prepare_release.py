"""Generate local deployment secrets once; never change .env or existing secrets."""
import os
from pathlib import Path
import secrets
from shared.config import ROOT_DIR


def main():
    target = ROOT_DIR / 'data' / 'release.env'
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print('기존 data/release.env를 보존합니다.')
        return
    with os.fdopen(fd, 'w') as output:
        output.write(f'JWT_SECRET={secrets.token_hex(32)}\nPOSTGRES_PASSWORD={secrets.token_hex(24)}\n')
        output.write('COLLECT_DISPATCH_ENABLED=true\nLANGSMITH_TRACING=false\n')
    print('data/release.env 생성 완료. .env 다음에 --env-file로 전달하세요.')


if __name__ == '__main__':
    main()

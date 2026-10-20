"""data/disclosures/<종목코드>.json의 body_text → rag_docs/companies/<종목코드>_<회사명>/<YYYYMMDD>_<공시종류>.md

사용법: uv run python scripts/build_rag_docs.py
원문(body_text)이 있는 공시만 만든다. 내용은 OpenDART 원문 그대로이며 가공하지 않는다.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "rag_docs" / "companies"

for path in sorted((ROOT / "data" / "disclosures").glob("*.json")):
    for d in json.loads(path.read_text(encoding="utf-8")):
        body = d.get("body_text")
        if not body:
            continue
        folder = OUT / f"{d['stock_code']}_{d['corp_name']}"
        folder.mkdir(parents=True, exist_ok=True)
        ymd = d["filed_at"].replace("-", "")
        # 같은 날 같은 종류가 여러 건일 수 있어 접수번호 뒷자리로 구분한다
        name = f"{ymd}_{d['disclosure_type']}_{d['rcept_no'][-6:]}.md"
        title = d["title"].replace('"', "'")
        (folder / name).write_text(
            f'---\nrcept_no: "{d["rcept_no"]}"\ncorp_name: {d["corp_name"]}\nstock_code: "{d["stock_code"]}"\n'
            f'filed_at: {d["filed_at"]}\ntitle: "{title}"\nurl: {d["url"]}\n---\n\n{body}\n',
            encoding="utf-8",
        )
print({p.name: len(list(p.glob("*.md"))) for p in OUT.iterdir() if p.is_dir()})

"""Explicit non-generation checks. Never print credentials or raw provider errors.

python -m scripts.ops.check_providers
This consumes a small DART lookup quota but creates no model/embedding output.
It does not certify an end-to-end analysis or model quality.
"""
import asyncio
from datetime import date
import json
from shared.config import get_settings


async def check():
    settings = get_settings()
    results = []
    if not settings.OPENAI_API_KEY:
        results.append({'provider':'openai','ok':False,'error_code':'KEY_MISSING'})
    else:
        from openai import AsyncOpenAI
        # Model metadata lookup is separate from paid generation/embedding.
        model = settings.LLM_PRIMARY if settings.LLM_PRIMARY.startswith('gpt') else settings.LLM_FALLBACK
        async with AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=15, max_retries=0) as client:
            for name in dict.fromkeys((model, settings.EMBEDDING_MODEL)):
                try:
                    await client.models.retrieve(name)
                    results.append({'provider':'openai','model':name,'ok':True})
                except Exception as exc:
                    results.append({'provider':'openai','model':name,'ok':False,
                                    'error_code':str(getattr(exc,'status_code',None) or type(exc).__name__)})
    if not settings.DART_API_KEY:
        results.append({'provider':'dart','ok':False,'error_code':'KEY_MISSING'})
    else:
        from collector.dart_client import DartClient
        try:
            async with DartClient.from_settings() as client:
                # Existing public company/date fixture; no issuer/customer secrets.
                await client.list_disclosures('00106119', date(2025,3,21), date(2025,3,21))
            results.append({'provider':'dart','ok':True})
        except Exception as exc:
            results.append({'provider':'dart','ok':False,
                            'error_code':str(getattr(exc,'status',None) or type(exc).__name__)})
    return results


if __name__ == '__main__':
    results = asyncio.run(check())
    print(json.dumps(results, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(row['ok'] for row in results) else 1)

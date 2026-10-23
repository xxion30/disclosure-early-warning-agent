"""DB execution host. R2 runtime factory must expose explicit public/private ports.

Usage: python -m scripts.ops.run_analysis_worker --factory package.module:factory
Factory(deps, initialized_saver) -> runtime.analyze_public(context), runtime.draft_review(context).
"""
import argparse
import asyncio
import importlib
from shared.config import get_settings
from api.worker.analysis import serve
from agent.adapters.assembly import build_dependencies
from agent.adapters.executors import open_executors


async def run(factory, once=False):
    deps=await build_dependencies()
    try:
        async with open_executors(factory,deps,get_settings().CHECKPOINT_DB) as ports:
            await serve(ports.public,once)
    finally:
        close=getattr(deps.tools,'close',None)
        if close is not None:await close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--factory',required=True,help='R2 runtime factory module:callable')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    module,name=args.factory.rsplit(':',1)
    factory=getattr(importlib.import_module(module),name)
    asyncio.run(run(factory,args.once))


if __name__=='__main__':main()

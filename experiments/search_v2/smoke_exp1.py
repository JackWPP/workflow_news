"""Smoke test for exp1: single Bocha call."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from exp1_bocha_params import call_bocha, analyze, query_balance
import httpx


async def smoke():
    async with httpx.AsyncClient() as client:
        bal = await query_balance(client)
        print(f"balance: {bal}")
        result = await call_bocha(client, "注塑机 新品发布", 10, True, "oneWeek")
        print(f"ok={result['ok']} latency={result['latency_ms']}ms err={result.get('error')}")
        if result["ok"]:
            metrics = analyze(result["raw_results"])
            print(f"metrics: {metrics}")
            if result["raw_results"]:
                first = result["raw_results"][0]
                print(f"sample title: {(first.get('name') or '')[:60]}")
                print(f"sample summary len: {len(first.get('summary') or '')}")
                print(f"sample snippet len: {len(first.get('snippet') or '')}")
                print(f"sample summary head: {(first.get('summary') or '')[:200]}")
                print(f"sample fields: {list(first.keys())}")


if __name__ == "__main__":
    asyncio.run(smoke())

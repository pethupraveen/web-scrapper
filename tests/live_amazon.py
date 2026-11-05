"""Verify the seven reported ASINs on a preview (--vercel) or public deployment."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ASINS = ["B0GL2ZGQLX", "B0GJZPRXF7", "B0FQBCWSW9", "B0FQBC1VQ2", "B0FQB8SFF4", "B0FQB9XZ48", "B0FQBGCZM2"]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://web-scrapper-umber.vercel.app")
    parser.add_argument("--vercel", action="store_true", help="Use Vercel CLI for a protected preview")
    args = parser.parse_args()
    artifact = Path(".vercel")
    artifact.mkdir(exist_ok=True)
    results = []
    with httpx.Client(timeout=280) as client:
        for asin in ASINS:
            body = {"kind": "amazon", "value": asin}
            start = time.monotonic()
            if args.vercel:
                request = artifact / "amazon-test-request.json"
                request.write_text(json.dumps(body), encoding="utf-8")
                result = subprocess.run(["npx.cmd", "--yes", "vercel", "curl",
                    args.url.rstrip("/") + "/api/cloud/scrape", "--", "--request", "POST",
                    "--header", "Content-Type: application/json", "--data-binary", "@" + str(request.resolve()),
                    "--silent", "--show-error"], capture_output=True, text=True, encoding="utf-8", check=True)
                data = json.loads(result.stdout)
            else:
                response = client.post(args.url.rstrip("/") + "/api/cloud/scrape", json=body)
                response.raise_for_status()
                data = response.json()
            row = data.get("rows", [{}])[0]
            record = {"asin": asin, "seconds": round(time.monotonic() - start, 1), **data}
            results.append(record)
            print(f"{asin}: {row.get('Status', data.get('error'))} ({record['seconds']}s); "
                  f"price={row.get('Price')}; rating={row.get('Rating')}; title={row.get('Title')}", flush=True)
            time.sleep(3)
    (artifact / "amazon-verification.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    succeeded = sum(item.get("rows", [{}])[0].get("Status") == "Success" for item in results)
    print(f"Verified {succeeded}/{len(ASINS)} products", flush=True)
    if succeeded != len(ASINS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

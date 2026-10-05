#!/usr/bin/env python
"""Download the committed Kaggle run's output files (all pages) into
vouch/out/v5-kaggle-run/, preserving the backtest-<tag>/ structure."""
import json
import os
import sys
import urllib.request

TOKEN = open(os.path.expanduser("~/.kaggle/access_token")).read().strip()
BASE = "https://www.kaggle.com/api/v1/kernels/output?userName=rennf93&kernelSlug=notebookb87eea35bd"
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out", "v5-kaggle-run")


def api(url: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def download(url: str, dest: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def main() -> None:
    os.makedirs(DEST, exist_ok=True)
    all_files = []
    page_url = BASE
    while page_url:
        d = api(page_url)
        all_files.extend(d.get("files", []))
        page_url = None
        if d.get("hasNextPageToken") and d.get("nextPageToken"):
            page_url = BASE + "&pageToken=" + d["nextPageToken"]
    print(f"files listed: {len(all_files)}")
    ok, fail = 0, []
    for f in all_files:
        name = f.get("fileName") or f.get("fileNameNullable")
        url = f.get("url") or f.get("urlNullable")
        if not name or not url:
            continue
        dest = os.path.join(DEST, name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            download(url, dest)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail.append((name, str(e)[:80]))
            print(f"FAIL {name}: {e}")
    print(f"downloaded: {ok}, failed: {len(fail)}")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()

# backend/scripts/fetch_sentiment_datasets.py
#
# Purpose: download the two labelled sentiment datasets for RQ1 (evaluation/stocks_rules.py) at their
# PINNED HuggingFace revisions into backend/evaluation/data/ (gitignored: licensed third-party data is
# never committed). Prints each file's SHA-256; the evaluation records the same hashes, so a reader
# can check they used identical files.
#
#   twitter_fin  zeroshot/twitter-financial-news-sentiment   MIT
#   phrasebank   takala/financial_phrasebank                 CC BY-NC-SA 3.0 (non-commercial, attribution:
#                                                            Malo et al. 2014, "Good debt or bad debt")
#
# Standard library only (urllib). Polite: one request per file, a timeout, a size cap, a pause.
# Run once while online (inside Docker):
#   docker compose exec backend python -m scripts.fetch_sentiment_datasets [--force]

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from evaluation.stocks_rules import RQ1_DATASETS

DATA_DIR = Path(__file__).resolve().parent.parent / "evaluation" / "data"
FILES: dict[str, tuple[str, ...]] = {
    "twitter_fin": ("sent_train.csv", "sent_valid.csv"),
    "phrasebank": ("data/FinancialPhraseBank-v1.0.zip",),
}
TIMEOUT_SECONDS = 30
MAX_BYTES = 5_000_000          # the largest file is about 0.9 MB
PAUSE_SECONDS = 1.5
USER_AGENT = "capstone-finance-advisor-evaluation"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target_path(key: str, remote_name: str) -> Path:
    """evaluation/data/<dataset key>/<file name> (folders in the remote path are dropped)."""
    return DATA_DIR / key / Path(remote_name).name


def download(url: str, destination: Path) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError(f"{url} is larger than {MAX_BYTES} bytes; refusing to save it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return len(content)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download the RQ1 labelled sentiment datasets (pinned revisions).")
    parser.add_argument("--force", action="store_true", help="download again even if the file exists")
    args = parser.parse_args()
    failures = 0
    for dataset in RQ1_DATASETS:
        print(f"{dataset.key}: {dataset.hf_id} @ {dataset.revision[:12]} (licence: {dataset.licence})")
        for remote in FILES[dataset.key]:
            path = target_path(dataset.key, remote)
            if path.exists() and not args.force:
                print(f"  {path.name}: already here, sha256 {sha256(path)}")
                continue
            url = f"https://huggingface.co/datasets/{dataset.hf_id}/resolve/{dataset.revision}/{remote}"
            try:
                size = download(url, path)
                print(f"  {path.name}: {size} bytes, sha256 {sha256(path)}")
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                failures += 1
                print(f"  {path.name}: FAILED ({type(exc).__name__}: {exc})")
            time.sleep(PAUSE_SECONDS)
    print("Done" if not failures else f"{failures} file(s) failed; RQ1 will skip what is missing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

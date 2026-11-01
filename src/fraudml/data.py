"""Download the public credit-card fraud dataset (OpenML id 1597)."""
import urllib.request

from .config import DATA_DIR, DATASET_URL, RAW_PATH


def download(force: bool = False) -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_PATH.exists() and not force:
        print(f"[data] already present: {RAW_PATH}")
        return str(RAW_PATH)
    print(f"[data] downloading {DATASET_URL}")
    urllib.request.urlretrieve(DATASET_URL, RAW_PATH)
    print(f"[data] saved {RAW_PATH} ({RAW_PATH.stat().st_size / 1e6:.1f} MB)")
    return str(RAW_PATH)


if __name__ == "__main__":
    download()

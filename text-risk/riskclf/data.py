"""UCI SMS Spam Collection (CC BY 4.0): 5,574 labelled SMS messages, 747 spam.

Source: https://archive.ics.uci.edu/dataset/228/sms+spam+collection
Exact duplicate texts are dropped before the split so no message appears in both train and test.
"""
import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

URL = "https://archive.ics.uci.edu/static/public/228/sms+spam+collection.zip"
SHA256 = "1587ea43e58e82b14ff1f5425c88e17f8496bfcdb67a583dbff9eefaf9963ce3"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SEED = 42


def download() -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    raw = DATA_DIR / "SMSSpamCollection"
    if raw.exists():
        return raw
    blob = urllib.request.urlopen(URL, timeout=60).read()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != SHA256:
        raise RuntimeError(f"unexpected sha256 {digest}")
    zipfile.ZipFile(io.BytesIO(blob)).extract("SMSSpamCollection", DATA_DIR)
    return raw


def load() -> pd.DataFrame:
    df = pd.read_csv(download(), sep="\t", header=None, names=["raw_label", "text"], quoting=3)
    df["label"] = (df.raw_label == "spam").map({True: "scam", False: "legit"})
    df["y"] = (df.label == "scam").astype(int)
    return df.drop_duplicates("text").reset_index(drop=True)[["text", "label", "y"]]


def split(test_size: float = 0.2):
    df = load()
    tr, te = train_test_split(df, test_size=test_size, stratify=df.y, random_state=SEED)
    return tr.reset_index(drop=True), te.reset_index(drop=True)

import os

from .db import DocStore
from .market import Market

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env(path=os.path.join(ROOT, ".env")):
    """KEY=VALUE lines from .env (machine-local secrets such as SEPA_TOKEN; never committed)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            k, sep, v = line.strip().partition("=")
            if sep and k and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
DB_PATH = os.environ.get("SEPA_DB", os.path.join(ROOT, "data", "desk.sqlite"))
SEED_DIR = os.path.join(ROOT, "data", "seed")


def open_store(seed=True):
    store = DocStore(DB_PATH)
    if seed and store.count() == 0 and os.path.isdir(SEED_DIR):
        store.import_dir(SEED_DIR)
    return store


def open_market():
    return Market(DB_PATH)

"""The DeepL key pasted in Settings, kept in its own file beside the database (never in it), so backups and the
export never carry it. DEEPL_AUTH_KEY in .env, when set, wins over it."""

import os
from pathlib import Path

KEY_PATH = Path("secrets") / "deepl-key"


def load_key(data_dir: Path) -> str:
    try:
        return (data_dir / KEY_PATH).read_text().strip()
    except FileNotFoundError:
        return ""


def save_key(data_dir: Path, key: str) -> None:
    key = key.strip()
    if not key:
        raise ValueError("Paste the key first")
    folder = data_dir / KEY_PATH.parent
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    partial = folder / ".deepl-key"
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # private from the first byte
    with os.fdopen(fd, "w") as f:
        f.write(key)
    partial.replace(data_dir / KEY_PATH)


def remove_key(data_dir: Path) -> None:
    (data_dir / KEY_PATH).unlink(missing_ok=True)


def mask(key: str) -> str:
    """Enough to recognise the key, not to use it."""
    return "•••• " + key[-4:]

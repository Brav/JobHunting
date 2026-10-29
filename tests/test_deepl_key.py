import stat

import pytest

from jobhunt.deepl_key import KEY_PATH, load_key, mask, remove_key, save_key


def test_key_saved_privately(tmp_path):
    save_key(tmp_path, "abc:fx")
    path = tmp_path / KEY_PATH
    assert path.read_text() == "abc:fx"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_key_is_stripped(tmp_path):
    save_key(tmp_path, "  abc:fx\n")
    assert load_key(tmp_path) == "abc:fx"


def test_empty_key_refused(tmp_path):
    with pytest.raises(ValueError, match="Paste the key first"):
        save_key(tmp_path, "  \n")
    assert not (tmp_path / KEY_PATH).exists()


def test_no_key(tmp_path):
    assert load_key(tmp_path) == ""


def test_mask():
    assert mask("0123-4567:fx") == "•••• 7:fx"


def test_remove_key(tmp_path):
    save_key(tmp_path, "abc:fx")
    remove_key(tmp_path)
    remove_key(tmp_path)  # nothing left: no error
    assert load_key(tmp_path) == ""

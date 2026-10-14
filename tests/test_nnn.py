import gzip
import os
import re
import shutil
import struct
import zlib
from pathlib import Path

import pytest

from deepwick_spec.ansi import runs
from deepwick_spec.check_port import check_port
from deepwick_spec.palette import load_palette
from deepwick_spec.tui import capture, tmux_available

REPO = Path(__file__).resolve().parents[1]
PALETTE = load_palette()
C = PALETTE.colors
SLOTS = ("blockDevice", "charDevice", "directory", "executable", "regular", "hardLink",
         "symlink", "details", "orphan", "fifo", "socket", "unknown")
EXPECTED = {
    "blockDevice": 3, "charDevice": 3, "directory": 4, "executable": 10, "regular": 0, "hardLink": 12,
    "symlink": 14, "details": 8, "orphan": 1, "fifo": 11, "socket": 13, "unknown": 1,
}


def isolated_env(tmp_path, **extra):
    dirs = {"HOME": tmp_path / "home"}
    for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
        dirs[f"XDG_{kind}_HOME"] = tmp_path / kind.lower()
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    env = {key: str(path) for key, path in dirs.items()}
    env.update({key: str(value) for key, value in extra.items()})
    return env


def style_containing(text, needle):
    for chunk, style in runs(text):
        if needle in chunk:
            return style
    raise AssertionError(f"no run contains {needle!r}")


TREE_ROLES = {
    "src": "directory", "run.sh": "executable", "link": "symlink", "broken": "brokenSymlink",
    "pipe": "special", "bundle.tar.gz": "archive", "photo.png": "media", "notes.pdf": "document",
    "app.py": "source", "old.bak": "temp",
}


def tiny_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b"")


def make_tree(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("def main():\n    return 0\n")
    (root / "run.sh").write_text("#!/bin/sh\necho run\n")
    (root / "run.sh").chmod(0o755)
    (root / "app.py").write_text("print('hi')\n")
    (root / "link").symlink_to("app.py")
    (root / "broken").symlink_to("missing")
    os.mkfifo(root / "pipe")
    (root / "bundle.tar.gz").write_bytes(gzip.compress(b"deepwick"))
    (root / "photo.png").write_bytes(tiny_png())
    (root / "notes.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (root / "old.bak").write_text("old\n")
    return root


def exports(path):
    assert path.is_file(), f"{path.name} missing"
    text = path.read_text(encoding="utf-8")
    pattern = r"""^(?:export (\w+)='([^']*)'|set -gx (\w+) '([^']*)')$"""
    found = {}
    for m in re.finditer(pattern, text, re.M):
        found[m.group(1) or m.group(3)] = m.group(2) or m.group(4)
    return found


def test_fcolors_format_and_slots():
    value = exports(REPO / "deepwick.sh")["NNN_FCOLORS"]
    assert re.fullmatch(r"(0[0-9a-f]){12}", value), "12 two-digit indices 00-0f, no # prefix"
    assert dict(zip(SLOTS, (int(value[i : i + 2], 16) for i in range(0, 24, 2)))) == EXPECTED


def test_context_colors_blue():
    assert exports(REPO / "deepwick.sh")["NNN_COLORS"] == "4444"


def test_fish_matches_sh():
    assert exports(REPO / "deepwick.fish") == exports(REPO / "deepwick.sh")


@pytest.mark.skipif(not (tmux_available() and shutil.which("nnn")), reason="requires tmux and nnn")
def test_nnn_renders_slots(tmp_path):
    tree = make_tree(tmp_path / "tree")
    env = isolated_env(tmp_path, TERM="tmux-256color", **exports(REPO / "deepwick.sh"))
    out = capture(f"cd {tree} && nnn -d", env, size=(120, 30), wait_for="run.sh")
    assert style_containing(out, "src")["fg"] == "ansi:4"
    assert style_containing(out, "run.sh")["fg"] == "ansi:10"
    assert style_containing(out, "broken")["fg"] == "ansi:1"
    assert style_containing(out, "pipe")["fg"] == "ansi:11"
    assert style_containing(out, "link")["fg"] == "ansi:14"


def test_port_conventions():
    assert check_port(REPO, PALETTE.allowed_hex()) == []

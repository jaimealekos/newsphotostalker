"""End-to-end pipeline test through the real CLI entrypoints.

Runs seed + run_once in a subprocess against an isolated temp config, then
inspects the resulting SQLite DB. This exercises the actual wiring (config
loading, DB init, adapters, runner, retention) without reload gymnastics.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(module, env, *args):
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_seed_and_run_once(tmp_path):
    data_dir = tmp_path / "data"
    db_path = data_dir / "test.db"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
mode: mock
data_dir: {data_dir}
db_url: sqlite:///{db_path}
default_cadence_minutes: 60
agencies:
  ap: {{enabled: true}}
  reuters: {{enabled: true, username: u@example.com, password: secret}}
  getty: {{enabled: true}}
"""
    )
    env = dict(os.environ, APP_CONFIG=str(cfg), INGEST_MODE="mock")

    seed = _run("scripts.seed", env)
    assert seed.returncode == 0, seed.stderr
    assert seed.stdout.count("creada") == 3, seed.stdout

    run = _run("scripts.run_once", env)
    assert run.returncode == 0, run.stderr
    assert run.stdout.count("[ok]") == 3

    # Inspect the DB directly.
    con = sqlite3.connect(db_path)
    searches = con.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
    assets = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    agencies = dict(con.execute("SELECT agency, COUNT(*) FROM assets GROUP BY agency").fetchall())
    con.close()

    assert searches == 3
    assert assets > 0
    # Las tres búsquedas de ejemplo: Reuters, Getty y AP.
    assert set(agencies) == {"ap", "reuters", "getty"}

    # Media files were written for at least one asset.
    media = list((data_dir / "media").rglob("preview.jpg"))
    assert media, "no preview images written"


def test_run_once_is_idempotent(tmp_path):
    data_dir = tmp_path / "data"
    db_path = data_dir / "test.db"
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"mode: mock\ndata_dir: {data_dir}\ndb_url: sqlite:///{db_path}\n"
        "agencies: {ap: {enabled: true}}\n"
    )
    env = dict(os.environ, APP_CONFIG=str(cfg), INGEST_MODE="mock")

    _run("scripts.seed", env)
    _run("scripts.run_once", env)
    con = sqlite3.connect(db_path)
    first = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    con.close()

    _run("scripts.run_once", env)  # second run: no new assets expected
    con = sqlite3.connect(db_path)
    second = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    con.close()

    assert first == second

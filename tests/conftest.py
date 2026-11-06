from __future__ import annotations

from pathlib import Path

import pytest

from brightwater import fixture


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("fixture")
    fixture.write_csv(fixture.build(seed=7, n=1200), out)
    return out


@pytest.fixture(scope="session")
def loaded_db(fixture_dir: Path):
    """A Postgres loaded with the default fixture. Skips if Postgres is not up."""
    from brightwater import db
    try:
        conn = db.connect()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {e}")
    db.init_schema(conn)
    db.load_csv(conn, fixture_dir)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def expectations() -> dict:
    return fixture.load_expectations()

"""
Shared pytest fixtures and base classes for InvoiceOCR test suite.
"""

import pytest
from pathlib import Path
import tempfile
import shutil
from src import config
from tests.db_setup import init_db_for_test

@pytest.fixture(autouse=True)
def temp_project_root_and_db():
    """
    Fixture providing isolated PROJECT_ROOT and database initialization for each test.
    """
    tmpdir = Path(tempfile.mkdtemp())
    output_dir = tmpdir / "output"
    output_dir.mkdir()
    input_dir = tmpdir / "input"
    input_dir.mkdir()
    processed_dir = tmpdir / "processed"
    processed_dir.mkdir()

    original_root = config.PROJECT_ROOT
    config.PROJECT_ROOT = tmpdir

    init_db_for_test()

    yield tmpdir

    config.PROJECT_ROOT = original_root
    shutil.rmtree(tmpdir, ignore_errors=True)

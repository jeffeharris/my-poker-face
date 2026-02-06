"""Shared fixtures for repository tests."""
import pytest

pytestmark = pytest.mark.integration
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database with schema initialized."""
    path = str(tmp_path / "test.db")
    SchemaManager(path).ensure_schema()
    return path

"""Pytest configuration and shared test fixtures."""

import pytest
from sqlalchemy.orm import sessionmaker

from src.models.orm import Base
from src.storage.database import create_db_engine
from src.storage.repository import PipelineRepository


@pytest.fixture
def in_memory_engine():
    """Create an isolated in-memory SQLite engine for unit testing."""
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(in_memory_engine):
    """Provide a transactional session for each unit test."""
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=in_memory_engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def repo(db_session):
    """Provide a PipelineRepository bound to the test session."""
    return PipelineRepository(db_session)

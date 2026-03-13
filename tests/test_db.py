import pytest
from db import test_connection


def test_db_connection():
    assert test_connection() is True

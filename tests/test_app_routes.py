import pytest
from app import app as flask_app


@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as client:
        yield client


def test_home(client):
    rv = client.get('/')
    assert rv.status_code == 200


def test_register_page(client):
    rv = client.get('/register')
    assert rv.status_code == 200


def test_login_page(client):
    rv = client.get('/login')
    assert rv.status_code == 200


def test_student_page(client):
    rv = client.get('/student')
    assert rv.status_code == 200

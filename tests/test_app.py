from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_renders_empty_inbox() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Posteingangs-Assistent" in response.text
    assert "Noch keine Eingänge vorhanden." in response.text


def test_index_shows_ai_usage_notice() -> None:
    response = client.get("/")

    assert "Hinweis zur KI-Nutzung" in response.text

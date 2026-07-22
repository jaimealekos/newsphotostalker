"""Autenticación: hash de contraseñas y tokens de sesión."""

from app import auth


def test_password_roundtrip():
    stored = auth.hash_password("secreto123")
    assert stored.startswith("pbkdf2$")
    assert "secreto123" not in stored
    assert auth.verify_password("secreto123", stored)
    assert not auth.verify_password("otra", stored)
    assert not auth.verify_password("secreto123", "basura")


def test_token_roundtrip(monkeypatch):
    monkeypatch.setattr(auth, "_secret", lambda: b"test-secret")
    token = auth.make_token(42, seconds=60)
    assert auth.verify_token(token) == 42


def test_token_tampered_or_expired(monkeypatch):
    monkeypatch.setattr(auth, "_secret", lambda: b"test-secret")
    token = auth.make_token(42, seconds=60)
    assert auth.verify_token(token.replace("42", "43", 1)) is None

    expired = auth.make_token(42, seconds=-10)
    assert auth.verify_token(expired) is None

    assert auth.verify_token("no.es.un.token") is None
    assert auth.verify_token("") is None

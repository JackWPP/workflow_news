from __future__ import annotations

from datetime import UTC, datetime

from app.models import AuthSession, User
from app.security import generate_session_token, hash_password, session_expiry, verify_password
from app.services.repository import get_session_by_token, get_user_by_email


def register_user(session, email: str, password: str) -> User:
    existing = get_user_by_email(session, email)
    if existing is not None:
        raise ValueError("Email already registered")

    user = User(email=email.lower().strip(), password_hash=hash_password(password), is_admin=False, is_active=True)
    session.add(user)
    session.flush()
    return user


def create_login_session(session, email: str, password: str) -> tuple[User, AuthSession]:
    user = get_user_by_email(session, email.lower().strip())
    if user is None or not verify_password(password, user.password_hash):
        raise ValueError("Invalid email or password")
    if not user.is_active:
        raise ValueError("User is inactive")

    auth_session = AuthSession(user_id=user.id, token=generate_session_token(), expires_at=session_expiry())
    session.add(auth_session)
    session.flush()
    return user, auth_session


def get_current_user(session, token: str | None) -> User | None:
    if not token:
        return None
    auth_session = get_session_by_token(session, token)
    if auth_session is None:
        return None
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        session.delete(auth_session)
        session.flush()
        return None
    return auth_session.user


def logout_session(session, token: str | None) -> None:
    auth_session = get_session_by_token(session, token) if token else None
    if auth_session is not None:
        session.delete(auth_session)
        session.flush()

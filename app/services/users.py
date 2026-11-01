"""Users. For now there is one default user, created at startup (F7.1)."""

from sqlmodel import Session, select

from app.db.models import User


def ensure_default_user(session: Session, email: str) -> User:
    """Return the default user, creating it the first time. Safe to call repeatedly."""
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user

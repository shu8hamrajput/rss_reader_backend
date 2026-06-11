"""Create (or reuse) a local test user and print a one-click login URL.

Run via the `seed-user` docker-compose service so local OAuth-less
environments have a way to obtain a valid JWT for the UI.
"""
from ..auth import create_access_token
from ..config import settings
from ..database import SessionLocal
from ..models import User

TEST_GOOGLE_ID = "local-test-user"
TEST_EMAIL = "test@example.com"
TEST_NAME = "Test User"


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.google_id == TEST_GOOGLE_ID).first()
        if not user:
            user = User(google_id=TEST_GOOGLE_ID, email=TEST_EMAIL, name=TEST_NAME)
            db.add(user)
            db.commit()
            db.refresh(user)

        token, _ = create_access_token(user.id, user.email, user.token_version)
        url = f"{settings.frontend_url}?token={token}"

        print("\n" + "=" * 70)
        print(f"Test user login: {url}")
        print("=" * 70 + "\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()

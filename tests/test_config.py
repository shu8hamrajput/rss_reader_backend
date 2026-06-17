import pytest

from app.config import Settings, _INSECURE_JWT_SECRET, _validate


def test_validate_rejects_default_jwt_secret():
    s = Settings(jwt_secret_key=_INSECURE_JWT_SECRET)
    with pytest.raises(RuntimeError):
        _validate(s)


def test_validate_accepts_real_jwt_secret():
    s = Settings(jwt_secret_key="a-unique-generated-secret")
    _validate(s)

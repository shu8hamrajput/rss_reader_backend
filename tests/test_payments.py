import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.config import settings
from app.models import Payment


def _signature(secret: str, *parts: str) -> str:
    body = "|".join(parts).encode()
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_create_order(client, db_session, user, auth_headers):
    with patch("app.routers.payments.razorpay_client.order.create", return_value={"id": "order_test123"}) as mock_create:
        resp = client.post("/api/v1/payments/orders", json={"plan": "paid"}, headers=auth_headers)

    assert resp.status_code == 201
    body = resp.json()
    assert body["order_id"] == "order_test123"
    assert body["currency"] == "INR"
    assert body["plan"] == "paid"
    mock_create.assert_called_once()

    payment = db_session.query(Payment).filter(Payment.razorpay_order_id == "order_test123").first()
    assert payment is not None
    assert payment.user_id == user.id
    assert payment.status == "created"


def test_create_order_unpurchasable_plan(client, auth_headers):
    resp = client.post("/api/v1/payments/orders", json={"plan": "free"}, headers=auth_headers)
    assert resp.status_code == 422


def test_create_order_razorpay_failure(client, auth_headers):
    with patch("app.routers.payments.razorpay_client.order.create", side_effect=Exception("razorpay down")):
        resp = client.post("/api/v1/payments/orders", json={"plan": "paid"}, headers=auth_headers)
    assert resp.status_code == 502


def test_verify_payment_success(client, db_session, user, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_secret", "test-secret")

    payment = Payment(user_id=user.id, plan="paid", amount=29900, currency="INR", razorpay_order_id="order_ok", status="created")
    db_session.add(payment)
    db_session.commit()

    sig = _signature("test-secret", "order_ok", "pay_123")
    resp = client.post(
        "/api/v1/payments/verify",
        json={"razorpay_order_id": "order_ok", "razorpay_payment_id": "pay_123", "razorpay_signature": sig},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["plan"] == "paid"

    db_session.refresh(user)
    db_session.refresh(payment)
    assert payment.status == "paid"
    assert user.plan_expires_at is not None
    assert user.plan_expires_at > datetime.now(timezone.utc) + timedelta(days=29)


def test_verify_payment_bad_signature(client, db_session, user, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_secret", "test-secret")

    payment = Payment(user_id=user.id, plan="paid", amount=29900, currency="INR", razorpay_order_id="order_bad_sig", status="created")
    db_session.add(payment)
    db_session.commit()

    resp = client.post(
        "/api/v1/payments/verify",
        json={"razorpay_order_id": "order_bad_sig", "razorpay_payment_id": "pay_123", "razorpay_signature": "wrong"},
        headers=auth_headers,
    )
    assert resp.status_code == 400

    db_session.refresh(payment)
    assert payment.status == "failed"


def test_verify_payment_order_not_found(client, auth_headers):
    resp = client.post(
        "/api/v1/payments/verify",
        json={"razorpay_order_id": "does-not-exist", "razorpay_payment_id": "pay_123", "razorpay_signature": "whatever"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_webhook_activates_payment(client, db_session, user, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "whsec")

    payment = Payment(user_id=user.id, plan="paid", amount=29900, currency="INR", razorpay_order_id="order_webhook", status="created")
    db_session.add(payment)
    db_session.commit()

    event = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_webhook_1", "order_id": "order_webhook"}}},
    }
    raw = json.dumps(event).encode()
    sig = hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()

    resp = client.post(
        "/api/v1/payments/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
    )
    assert resp.status_code == 200

    db_session.refresh(payment)
    db_session.refresh(user)
    assert payment.status == "paid"
    assert user.plan == "paid"


def test_webhook_bad_signature(client, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "whsec")

    raw = json.dumps({"event": "payment.captured", "payload": {}}).encode()
    resp = client.post(
        "/api/v1/payments/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": "wrong"},
    )
    assert resp.status_code == 400


def test_activation_idempotent(client, db_session, user, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_secret", "test-secret")
    monkeypatch.setattr(settings, "razorpay_webhook_secret", "whsec")

    payment = Payment(user_id=user.id, plan="paid", amount=29900, currency="INR", razorpay_order_id="order_idem", status="created")
    db_session.add(payment)
    db_session.commit()

    sig = _signature("test-secret", "order_idem", "pay_idem")
    client.post(
        "/api/v1/payments/verify",
        json={"razorpay_order_id": "order_idem", "razorpay_payment_id": "pay_idem", "razorpay_signature": sig},
        headers=auth_headers,
    )
    db_session.refresh(user)
    first_expiry = user.plan_expires_at

    event = {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_idem", "order_id": "order_idem"}}},
    }
    raw = json.dumps(event).encode()
    webhook_sig = hmac.new(b"whsec", raw, hashlib.sha256).hexdigest()
    resp = client.post(
        "/api/v1/payments/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": webhook_sig},
    )
    assert resp.status_code == 200

    db_session.refresh(user)
    assert user.plan_expires_at == first_expiry

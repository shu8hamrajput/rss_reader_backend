"""
Razorpay payment integration — purchase / renew a subscription plan.

Flow:
  1. POST /payments/orders   → create a Razorpay order; client opens Checkout with it
  2. Razorpay Checkout       → user pays; client receives order/payment/signature
  3. POST /payments/verify   → verify the signature, activate the plan immediately
  4. POST /payments/webhook  → Razorpay's async confirmation — defence in depth for
                               cases where the client never calls /verify (e.g. closed
                               the tab mid-checkout but the payment still went through)

Both /verify and the webhook funnel through `_activate_plan`, which is
idempotent (paid orders are skipped on a second activation attempt).
"""
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Payment, User
from ..schemas import PaymentOrderCreate, PaymentOrderResponse, PaymentVerifyRequest, UserResponse
from ..services.plans import PLAN_PRICING
from ..services.razorpay_client import razorpay_client

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger(__name__)


@router.post(
    "/orders",
    response_model=PaymentOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Razorpay order to purchase or renew a plan",
)
def create_order(
    payload: PaymentOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pricing = PLAN_PRICING.get(payload.plan)
    if not pricing:
        raise HTTPException(status_code=422, detail=f"Plan '{payload.plan}' is not purchasable")

    try:
        order = razorpay_client.order.create({
            "amount": pricing.amount,
            "currency": pricing.currency,
            "notes": {"user_id": str(current_user.id), "plan": payload.plan},
        })
    except Exception as exc:
        logger.warning("Razorpay order creation failed for user %d: %s", current_user.id, exc)
        raise HTTPException(status_code=502, detail="Could not create payment order")

    db.add(Payment(
        user_id=current_user.id,
        plan=payload.plan,
        amount=pricing.amount,
        currency=pricing.currency,
        razorpay_order_id=order["id"],
        status="created",
    ))
    db.commit()

    return PaymentOrderResponse(
        order_id=order["id"],
        amount=pricing.amount,
        currency=pricing.currency,
        key_id=settings.razorpay_key_id,
        plan=payload.plan,
    )


def _verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """HMAC-SHA256 of "order_id|payment_id" signed with the key secret — see
    https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/build-integration/#step-3-verify-payment-signature"""
    body = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(settings.razorpay_key_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _activate_plan(payment: Payment, payment_id: str, signature: str | None, db: Session) -> None:
    """Mark *payment* paid and extend the owning user's plan. Idempotent — a
    payment that's already marked paid (e.g. /verify beat the webhook to it) is left alone."""
    if payment.status == "paid":
        return

    payment.razorpay_payment_id = payment_id
    payment.razorpay_signature = signature
    payment.status = "paid"

    user = payment.user
    pricing = PLAN_PRICING[payment.plan]
    now = datetime.now(timezone.utc)
    # Renewing before expiry extends the existing period rather than restarting it
    base = user.plan_expires_at if (user.plan_expires_at and user.plan_expires_at > now) else now
    user.plan = payment.plan
    user.plan_expires_at = base + timedelta(days=pricing.duration_days)

    db.commit()
    logger.info("Activated plan '%s' for user %d until %s", user.plan, user.id, user.plan_expires_at)


@router.post(
    "/verify",
    response_model=UserResponse,
    summary="Verify a completed Razorpay checkout and activate the plan",
)
def verify_payment(
    payload: PaymentVerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payment = (
        db.query(Payment)
        .filter(Payment.razorpay_order_id == payload.razorpay_order_id, Payment.user_id == current_user.id)
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Order not found")

    if not _verify_checkout_signature(payload.razorpay_order_id, payload.razorpay_payment_id, payload.razorpay_signature):
        payment.status = "failed"
        db.commit()
        raise HTTPException(status_code=400, detail="Payment signature verification failed")

    _activate_plan(payment, payload.razorpay_payment_id, payload.razorpay_signature, db)
    db.refresh(current_user)
    return UserResponse.model_validate(current_user)


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Razorpay webhook — async payment confirmation",
    description=(
        "Configure this URL in the Razorpay Dashboard (Settings → Webhooks) "
        "subscribed to the `payment.captured` event, with the same secret as "
        "`RAZORPAY_WEBHOOK_SECRET`. Not user-authenticated — verified via HMAC "
        "signature on the raw request body instead."
    ),
)
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(settings.razorpay_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event = await request.json()
    if event.get("event") == "payment.captured":
        entity = event.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = entity.get("order_id")
        payment = db.query(Payment).filter(Payment.razorpay_order_id == order_id).first()
        if payment:
            _activate_plan(payment, entity.get("id", ""), None, db)

    return {"status": "ok"}

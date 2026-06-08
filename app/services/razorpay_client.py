"""Shared Razorpay SDK client — see routers.payments for the checkout flow."""
import razorpay

from ..config import settings

razorpay_client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))

from django.db import transaction
from django.core.exceptions import ValidationError
from commerce.payment_services import initiate_payment, record_verified_payment
from commerce.checkout_services import checkout_total_paid
from .services import money


@transaction.atomic
def settle_split_checkout(checkout, user, cash, route_id=None):
    cash = money(cash)
    if checkout_total_paid(checkout):
        raise ValidationError(
            "This checkout already has a payment; review it before changing tenders."
        )
    if cash <= 0 or cash >= checkout.amount:
        raise ValidationError(
            "Cash portion must be greater than zero and less than the total."
        )
    receipt = None
    for method, amount in [("cash", cash), ("transfer", checkout.amount - cash)]:
        payment = initiate_payment(
            checkout=checkout,
            method=method,
            tender_amount=amount,
            surface="pos",
            transfer_route_id=route_id,
            idempotency_key=f"split:{checkout.public_id}:{method}",
        )
        receipt, _ = record_verified_payment(
            payment=payment,
            amount=amount,
            actor=user,
            idempotency_key=f"split-receipt:{checkout.public_id}:{method}",
            staff_pos_confirmed=method == "transfer",
            note="Cashier-confirmed split tender",
        )
    return receipt

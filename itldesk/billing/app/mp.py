"""Mercado Pago: Checkout Pro (Pix, boleto e cartão) por fatura + confirmação de pagamento."""
import hashlib
import hmac
import logging
import re

import httpx

from . import config

log = logging.getLogger("itldesk.mp")
API = "https://api.mercadopago.com"
REF_PREFIX = "itldesk-inv-"


def enabled() -> bool:
    return bool(config.MP_ACCESS_TOKEN)


_test_account = None


def is_test() -> bool:
    """Credencial TEST-... ou conta de usuário de teste do Mercado Pago (APP_USR de test_user)."""
    global _test_account
    if not enabled():
        return False
    if config.MP_ACCESS_TOKEN.startswith("TEST-"):
        return True
    if _test_account is None:
        try:
            r = httpx.get(f"{API}/users/me", headers=_headers(), timeout=10)
            _test_account = "test_user" in (r.json().get("tags") or [])
        except Exception:  # noqa: BLE001 - na dúvida, não mostra o aviso; tenta de novo depois
            return False
    return _test_account


def _headers(idempotency: str = "") -> dict:
    h = {"Authorization": f"Bearer {config.MP_ACCESS_TOKEN}"}
    if idempotency:
        h["X-Idempotency-Key"] = idempotency
    return h


def external_reference(invoice_id: int) -> str:
    return f"{REF_PREFIX}{invoice_id}"


def invoice_id_from_reference(ref: str):
    if ref and ref.startswith(REF_PREFIX) and ref[len(REF_PREFIX):].isdigit():
        return int(ref[len(REF_PREFIX):])
    return None


def create_preference(invoice: dict, payer_email: str, public_url: str) -> dict:
    """Cria o link de pagamento da fatura. Retorna {'id', 'url'}."""
    body = {
        "items": [{
            "id": f"fatura-{invoice['id']}",
            "title": invoice["description"][:250],
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": round(invoice["amount"] / 100, 2),
        }],
        "external_reference": external_reference(invoice["id"]),
        "notification_url": f"{config.PUBLIC_URL}{config.BASE}/webhooks/mercadopago",
        "back_urls": {"success": public_url, "pending": public_url, "failure": public_url},
        "auto_return": "approved",
        "statement_descriptor": "ITLDESK",
    }
    if payer_email and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", payer_email):
        body["payer"] = {"email": payer_email}
    r = httpx.post(
        f"{API}/checkout/preferences",
        json=body,
        headers=_headers(f"pref-{invoice['public_token']}"),
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    url = data.get("init_point") or data.get("sandbox_init_point")
    return {"id": data["id"], "url": url}


def get_payment(payment_id: str):
    r = httpx.get(f"{API}/v1/payments/{payment_id}", headers=_headers(), timeout=20)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def find_approved_payment(invoice_id: int):
    r = httpx.get(
        f"{API}/v1/payments/search",
        params={"external_reference": external_reference(invoice_id), "status": "approved"},
        headers=_headers(),
        timeout=20,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    return results[0] if results else None


METHOD_NAMES = {
    "pix": "Pix",
    "bank_transfer": "Pix",
    "ticket": "Boleto",
    "credit_card": "Cartão de crédito",
    "debit_card": "Cartão de débito",
    "account_money": "Saldo Mercado Pago",
}


def method_name(payment: dict) -> str:
    pm = payment.get("payment_method_id") or ""
    pt = payment.get("payment_type_id") or ""
    if pm == "pix":
        return "Pix"
    return METHOD_NAMES.get(pt, pt or pm or "Mercado Pago")


def valid_signature(x_signature: str, x_request_id: str, data_id: str) -> bool:
    """Valida o cabeçalho x-signature do webhook (só quando MP_WEBHOOK_SECRET está configurado)."""
    if not config.MP_WEBHOOK_SECRET:
        return True
    parts = dict(p.strip().split("=", 1) for p in (x_signature or "").split(",") if "=" in p)
    ts, v1 = parts.get("ts"), parts.get("v1")
    if not ts or not v1:
        return False
    did = data_id.lower() if data_id.isalnum() else data_id
    manifest = f"id:{did};request-id:{x_request_id};ts:{ts};"
    expected = hmac.new(config.MP_WEBHOOK_SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)

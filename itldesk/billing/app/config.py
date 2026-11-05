import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


HOST = os.environ.get("ITLDESK_HOST", "localhost")
PUBLIC_URL = os.environ.get("BILLING_PUBLIC_URL", f"https://{HOST}").rstrip("/")
BASE = "/cobranca"

SECRET_KEY = os.environ.get("BILLING_SECRET_KEY") or os.environ.get("ITLDESK_JWT_KEY", "")

BILLING_DB = os.environ.get("BILLING_DB", "/billing/billing.db")
RDAPI_DB = os.environ.get("RDAPI_DB", "/rdapi/rustdeskapi.db")
ALLOWED_FILE = os.environ.get("ALLOWED_FILE", "/data/itldesk_allowed_users.json")

MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "").strip()
MP_WEBHOOK_SECRET = os.environ.get("MP_WEBHOOK_SECRET", "").strip()

GRACE_DAYS = _int("BILLING_GRACE_DAYS", 5)
INVOICE_DAYS_BEFORE = _int("BILLING_INVOICE_DAYS_BEFORE", 7)
TRIAL_DAYS = _int("BILLING_TRIAL_DAYS", 7)
JOB_INTERVAL_SECONDS = _int("BILLING_JOB_INTERVAL", 300)

# Site de vendas
SIGNUP_MODE = os.environ.get("BILLING_SIGNUP_MODE", "trial")  # trial | pending (pagar antes de liberar)
SIGNUPS_PER_IP_PER_DAY = _int("BILLING_SIGNUPS_PER_IP_PER_DAY", 3)
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "/downloads")
SUPPORT_WHATSAPP = os.environ.get("SUPPORT_WHATSAPP", "").strip()   # ex.: 5511999999999
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "").strip()
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Unity Automações")
# cliente (itldesk) e servidor + cobrança (itldesk-server)
SOURCE_URL = os.environ.get("SOURCE_URL", "https://github.com/unityautomacoes")

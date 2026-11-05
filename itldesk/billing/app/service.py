"""Regras de cobrança: assinaturas, faturas, carência, bloqueio e lista de técnicos liberados."""
import calendar
import json
import logging
import os
import secrets
import tempfile
from datetime import date, timedelta

from . import config, db, mp

log = logging.getLogger("itldesk.billing")

STATUS_LABELS = {
    "pending": "Aguardando 1º pagamento",
    "trial": "Teste grátis",
    "active": "Ativa",
    "past_due": "Vencida (carência)",
    "blocked": "Bloqueada",
    "canceled": "Cancelada",
}
# assinaturas cujos técnicos podem iniciar acessos
ALLOWED_STATUSES = ("trial", "active", "past_due")
CYCLE_LABELS = {"monthly": "Mensal", "yearly": "Anual"}


def today() -> date:
    return date.today()


def parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def cycle_end(start: date, cycle: str) -> date:
    """Último dia de um período que começa em `start`."""
    return add_months(start, 12 if cycle == "yearly" else 1) - timedelta(days=1)


def plan_price(plan, cycle: str) -> int:
    return plan["price_yearly"] if cycle == "yearly" else plan["price_monthly"]


def money(cents) -> str:
    if cents is None:
        return "-"
    s = f"{cents / 100:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def br_date(s) -> str:
    if not s:
        return "-"
    d = parse_date(str(s))
    return d.strftime("%d/%m/%Y")


# ---------------------------------------------------------------- faturas

def public_invoice_url(token: str) -> str:
    return f"{config.PUBLIC_URL}{config.BASE}/fatura/{token}"


def create_invoice(b, sub, plan, customer_name: str, period_start: date) -> int:
    period_end = cycle_end(period_start, sub["cycle"])
    desc = (
        f"ITLDesk {plan['name']} ({CYCLE_LABELS.get(sub['cycle'], sub['cycle'])}) - {customer_name} - "
        f"{period_start.strftime('%d/%m/%Y')} a {period_end.strftime('%d/%m/%Y')}"
    )
    cur = b.execute(
        "INSERT INTO invoices (subscription_id, public_token, description, amount, due_date,"
        " period_start, period_end, status, created_at) VALUES (?,?,?,?,?,?,?,'pending',?)",
        (
            sub["id"], secrets.token_urlsafe(18), desc, plan_price(plan, sub["cycle"]),
            period_start.isoformat(), period_start.isoformat(), period_end.isoformat(), db.now_iso(),
        ),
    )
    db.log_event(b, sub["customer_id"], f"Fatura #{cur.lastrowid} gerada: {money(plan_price(plan, sub['cycle']))}")
    return cur.lastrowid


def _sub_context(b, sub_id: int):
    sub = b.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    plan = b.execute("SELECT * FROM plans WHERE id = ?", (sub["plan_id"],)).fetchone()
    cust = b.execute("SELECT * FROM customers WHERE id = ?", (sub["customer_id"],)).fetchone()
    return sub, plan, cust


def ensure_open_invoice(b, sub_id: int):
    """Garante a fatura do próximo período (ou a primeira, para assinaturas pendentes)."""
    sub, plan, cust = _sub_context(b, sub_id)
    if sub["status"] == "canceled":
        return None
    # no máximo uma fatura em aberto por assinatura
    row = b.execute(
        "SELECT id FROM invoices WHERE subscription_id = ? AND status = 'pending'", (sub_id,)
    ).fetchone()
    if row:
        return row["id"]
    if sub["status"] == "pending":
        return create_invoice(b, sub, plan, cust["name"], today())
    next_start = parse_date(sub["period_end"]) + timedelta(days=1)
    row = b.execute(
        "SELECT id FROM invoices WHERE subscription_id = ? AND period_start = ? AND status IN ('pending','paid')",
        (sub_id, next_start.isoformat()),
    ).fetchone()
    return row["id"] if row else create_invoice(b, sub, plan, cust["name"], next_start)


def ensure_payment_link(invoice_id: int):
    """Cria o link do Mercado Pago da fatura, se ainda não existir. Retorna a URL ou None."""
    with db.billing() as b:
        inv = b.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not inv or inv["status"] != "pending":
            return None
        if inv["payment_url"]:
            return inv["payment_url"]
        cust = b.execute(
            "SELECT c.email FROM customers c JOIN subscriptions s ON s.customer_id = c.id WHERE s.id = ?",
            (inv["subscription_id"],),
        ).fetchone()
    if not mp.enabled():
        return None
    try:
        pref = mp.create_preference(dict(inv), cust["email"] if cust else "", public_invoice_url(inv["public_token"]))
    except Exception as e:  # noqa: BLE001 - falha de rede/credencial não pode derrubar a tela
        log.warning("Mercado Pago: erro ao criar link da fatura %s: %s", invoice_id, e)
        return None
    with db.billing() as b:
        b.execute(
            "UPDATE invoices SET mp_preference_id = ?, payment_url = ? WHERE id = ?",
            (pref["id"], pref["url"], invoice_id),
        )
    return pref["url"]


def mark_paid(invoice_id: int, method: str, mp_payment_id: str = None, by: str = "") -> bool:
    with db.billing() as b:
        inv = b.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not inv or inv["status"] == "paid":
            return False
        sub, plan, cust = _sub_context(b, inv["subscription_id"])
        if sub["status"] in ("pending", "blocked", "canceled"):
            # sem acesso no momento: o novo período começa no dia do pagamento
            start = today()
            end = cycle_end(start, sub["cycle"])
        else:
            start = parse_date(inv["period_start"])
            end = max(parse_date(inv["period_end"]), parse_date(sub["period_end"]))
        b.execute(
            "UPDATE invoices SET status = 'paid', paid_at = ?, paid_method = ?, mp_payment_id = ?,"
            " period_start = ?, period_end = ? WHERE id = ?",
            (db.now_iso(), method, mp_payment_id, start.isoformat(), end.isoformat(), invoice_id),
        )
        b.execute(
            "UPDATE subscriptions SET status = 'active', period_end = ?, updated_at = ? WHERE id = ?",
            (end.isoformat(), db.now_iso(), sub["id"]),
        )
        # outras faturas abertas do mesmo período deixam de valer
        b.execute(
            "UPDATE invoices SET status = 'canceled' WHERE subscription_id = ? AND status = 'pending' AND period_start <= ?",
            (sub["id"], end.isoformat()),
        )
        extra = f" por {by}" if by else ""
        db.log_event(
            b, cust["id"],
            f"Fatura #{invoice_id} paga ({method}){extra}. Assinatura ativa até {end.strftime('%d/%m/%Y')}",
        )
    write_allowed_file()
    return True


def check_mp_payment(payment_id: str) -> bool:
    """Confere um pagamento direto na API do Mercado Pago (usado pelo webhook)."""
    payment = mp.get_payment(payment_id)
    if not payment or payment.get("status") != "approved":
        return False
    invoice_id = mp.invoice_id_from_reference(payment.get("external_reference") or "")
    if not invoice_id:
        return False
    return mark_paid(invoice_id, mp.method_name(payment), str(payment.get("id")))


# ---------------------------------------------------------------- assinaturas

def create_customer(name, document, email, phone, notes, plan_id, cycle, start_mode, by="") -> int:
    group_id = db.rd_create_group(f"Cliente: {name}")
    with db.billing() as b:
        cur = b.execute(
            "INSERT INTO customers (name, document, email, phone, notes, rd_group_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (name, document, email, phone, notes, group_id, db.now_iso()),
        )
        cid = cur.lastrowid
        t = today()
        if start_mode == "trial":
            status, end = "trial", t + timedelta(days=config.TRIAL_DAYS - 1)
        elif start_mode == "paid":
            status, end = "active", cycle_end(t, cycle)
        else:
            status, end = "pending", t - timedelta(days=1)
        cur = b.execute(
            "INSERT INTO subscriptions (customer_id, plan_id, cycle, status, period_end, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (cid, plan_id, cycle, status, end.isoformat(), db.now_iso(), db.now_iso()),
        )
        sub_id = cur.lastrowid
        db.log_event(b, cid, f"Cliente cadastrado ({STATUS_LABELS[status]}){' por ' + by if by else ''}")
        if start_mode == "paid":
            sub, plan, cust = _sub_context(b, sub_id)
            inv_id = create_invoice(b, sub, plan, name, t)
            b.execute(
                "UPDATE invoices SET status = 'paid', paid_at = ?, paid_method = 'Manual' WHERE id = ?",
                (db.now_iso(), inv_id),
            )
        if start_mode in ("trial", "pending"):
            ensure_open_invoice(b, sub_id)
    write_allowed_file()
    return cid


def change_plan(customer_id: int, plan_id: int, cycle: str, by=""):
    with db.billing() as b:
        sub = b.execute("SELECT * FROM subscriptions WHERE customer_id = ?", (customer_id,)).fetchone()
        plan = b.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not sub or not plan:
            return
        b.execute(
            "UPDATE subscriptions SET plan_id = ?, cycle = ?, updated_at = ? WHERE id = ?",
            (plan_id, cycle, db.now_iso(), sub["id"]),
        )
        # faturas em aberto são refeitas com o novo valor
        b.execute(
            "UPDATE invoices SET status = 'canceled' WHERE subscription_id = ? AND status = 'pending'", (sub["id"],)
        )
        db.log_event(b, customer_id, f"Plano alterado para {plan['name']} ({CYCLE_LABELS[cycle]}){' por ' + by if by else ''}")
    run_cycle(poll_mp=False)


def set_status(customer_id: int, action: str, by=""):
    with db.billing() as b:
        sub = b.execute("SELECT * FROM subscriptions WHERE customer_id = ?", (customer_id,)).fetchone()
        if not sub:
            return
        if action == "cancel":
            b.execute("UPDATE subscriptions SET status = 'canceled', updated_at = ? WHERE id = ?", (db.now_iso(), sub["id"]))
            b.execute("UPDATE invoices SET status = 'canceled' WHERE subscription_id = ? AND status = 'pending'", (sub["id"],))
            db.log_event(b, customer_id, f"Assinatura cancelada{' por ' + by if by else ''}")
        elif action == "reactivate" and sub["status"] == "canceled":
            b.execute(
                "UPDATE subscriptions SET status = 'pending', period_end = ?, updated_at = ? WHERE id = ?",
                ((today() - timedelta(days=1)).isoformat(), db.now_iso(), sub["id"]),
            )
            db.log_event(b, customer_id, f"Assinatura reaberta, aguardando pagamento{' por ' + by if by else ''}")
    run_cycle(poll_mp=False)


def set_period_end(customer_id: int, new_end: date, by=""):
    """Ajuste manual do vencimento (ex.: promessa de pagamento)."""
    with db.billing() as b:
        sub = b.execute("SELECT * FROM subscriptions WHERE customer_id = ?", (customer_id,)).fetchone()
        if not sub or sub["status"] == "canceled":
            return
        status = sub["status"]
        if new_end >= today() and status in ("past_due", "blocked", "pending"):
            status = "active"
        b.execute(
            "UPDATE subscriptions SET period_end = ?, status = ?, updated_at = ? WHERE id = ?",
            (new_end.isoformat(), status, db.now_iso(), sub["id"]),
        )
        b.execute("UPDATE invoices SET status = 'canceled' WHERE subscription_id = ? AND status = 'pending'", (sub["id"],))
        db.log_event(b, customer_id, f"Vencimento ajustado para {new_end.strftime('%d/%m/%Y')}{' por ' + by if by else ''}")
    run_cycle(poll_mp=False)


def run_cycle(poll_mp: bool = True):
    """Rotina periódica: vencimento, carência, bloqueio, novas faturas e confirmação de pagamentos."""
    t = today()
    with db.billing() as b:
        subs = b.execute("SELECT * FROM subscriptions WHERE status != 'canceled'").fetchall()
        for sub in subs:
            end = parse_date(sub["period_end"])
            status = sub["status"]
            if status in ("trial", "active") and t > end:
                status = "past_due"
            if status == "past_due" and t > end + timedelta(days=config.GRACE_DAYS):
                status = "blocked"
            if status != sub["status"]:
                b.execute(
                    "UPDATE subscriptions SET status = ?, updated_at = ? WHERE id = ?",
                    (status, db.now_iso(), sub["id"]),
                )
                db.log_event(b, sub["customer_id"], f"Assinatura: {STATUS_LABELS[sub['status']]} -> {STATUS_LABELS[status]}")
            if status == "pending" or t >= end - timedelta(days=config.INVOICE_DAYS_BEFORE):
                ensure_open_invoice(b, sub["id"])
        pending = [r["id"] for r in b.execute("SELECT id FROM invoices WHERE status = 'pending'")]
    if mp.enabled():
        for inv_id in pending:
            if poll_mp:
                try:
                    payment = mp.find_approved_payment(inv_id)
                    if payment:
                        mark_paid(inv_id, mp.method_name(payment), str(payment.get("id")))
                        continue
                except Exception as e:  # noqa: BLE001
                    log.warning("Mercado Pago: erro ao consultar fatura %s: %s", inv_id, e)
            ensure_payment_link(inv_id)
    write_allowed_file()


def allowed_user_ids():
    ids = set(db.rd_admin_ids())
    with db.billing() as b:
        rows = b.execute(
            "SELECT c.rd_group_id, p.max_technicians FROM subscriptions s"
            " JOIN customers c ON c.id = s.customer_id JOIN plans p ON p.id = s.plan_id"
            f" WHERE s.status IN ({','.join('?' * len(ALLOWED_STATUSES))})",
            ALLOWED_STATUSES,
        ).fetchall()
    for r in rows:
        users = [u for u in db.rd_group_users(r["rd_group_id"]) if u["status"] == 1]
        ids.update(u["id"] for u in users[: r["max_technicians"]])
    return sorted(ids)


def write_allowed_file():
    try:
        ids = allowed_user_ids()
    except Exception as e:  # noqa: BLE001 - sem lista nova, o hbbs segue com a anterior
        log.error("Erro ao montar lista de técnicos liberados: %s", e)
        return
    path = config.ALLOWED_FILE
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".allowed-")
    with os.fdopen(fd, "w") as f:
        json.dump({"generated_at": db.now_iso(), "user_ids": ids}, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)

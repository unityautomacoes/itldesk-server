#!/usr/bin/env python3
"""Testa o fluxo de cobrança numa CÓPIA dos bancos (não altera dados reais).

Uso:  docker exec -i itldesk-billing python3 - < itldesk/tests/billing_flow_check.py
Cria um cliente em teste grátis, técnicos, simula vencimento, carência, bloqueio e pagamento.
"""
import json
import os
import shutil
import sys
from datetime import timedelta

T = "/tmp/itldesk-flow-test"
shutil.rmtree(T, ignore_errors=True)
os.makedirs(T)
shutil.copy("/rdapi/rustdeskapi.db", f"{T}/rd.db")
os.environ.update(BILLING_DB=f"{T}/billing.db", RDAPI_DB=f"{T}/rd.db", ALLOWED_FILE=f"{T}/allowed.json", MP_ACCESS_TOKEN="")
sys.path.insert(0, "/srv")

from app import config, db, service  # noqa: E402

failures = 0


def check(name, cond):
    global failures
    failures += 0 if cond else 1
    print(("OK   " if cond else "FALHA") + " " + name)


def allowed():
    return set(json.load(open(config.ALLOWED_FILE))["user_ids"])


def sub_of(cid):
    with db.billing() as b:
        return dict(b.execute("SELECT * FROM subscriptions WHERE customer_id = ?", (cid,)).fetchone())


def set_end(cid, d):
    with db.billing() as b:
        b.execute("UPDATE subscriptions SET period_end = ? WHERE customer_id = ?", (d.isoformat(), cid))


db.init()
with db.billing() as b:
    basic = b.execute("SELECT * FROM plans WHERE name = 'Básico'").fetchone()
check("planos padrão criados (Básico: 1 técnico, R$ 49,00)", basic and basic["max_technicians"] == 1 and basic["price_monthly"] == 4900)

today = service.today()
cid = service.create_customer("Cliente Teste", "00.000.000/0001-00", "teste@example.com", "", "", basic["id"], "monthly", "trial")
s = sub_of(cid)
check("cliente em teste grátis", s["status"] == "trial")
with db.billing() as b:
    group = b.execute("SELECT rd_group_id FROM customers WHERE id = ?", (cid,)).fetchone()[0]
    inv = b.execute("SELECT * FROM invoices WHERE subscription_id = ? AND status = 'pending'", (s["id"],)).fetchone()
check("grupo criado no painel", bool(group))
check("fatura do 1º mês gerada para o fim do teste", inv is not None and inv["amount"] == 4900)

u1 = db.rd_create_user("teste.tec1", "senha12345", "Tec 1", "", group)
u2 = db.rd_create_user("teste.tec2", "senha12345", "Tec 2", "", group)
service.write_allowed_file()
a = allowed()
check("admin do painel sempre liberado", set(db.rd_admin_ids()) <= a)
check("1º técnico liberado no teste grátis", u1 in a)
check("2º técnico fora do limite do plano Básico", u2 not in a)

set_end(cid, today - timedelta(days=1))
service.run_cycle(poll_mp=False)
check("venceu ontem -> carência", sub_of(cid)["status"] == "past_due" and u1 in allowed())

set_end(cid, today - timedelta(days=config.GRACE_DAYS + 1))
service.run_cycle(poll_mp=False)
check(f"passou {config.GRACE_DAYS} dias -> bloqueado", sub_of(cid)["status"] == "blocked" and u1 not in allowed())

with db.billing() as b:
    open_inv = b.execute("SELECT id FROM invoices WHERE subscription_id = ? AND status = 'pending'", (s["id"],)).fetchall()
check("existe 1 fatura em aberto", len(open_inv) == 1)
service.mark_paid(open_inv[0]["id"], "Pix")
s = sub_of(cid)
check("pagou -> ativo de novo", s["status"] == "active" and u1 in allowed())
check("novo período começa no dia do pagamento", s["period_end"] == service.cycle_end(today, "monthly").isoformat())
check("pagamento repetido é ignorado", service.mark_paid(open_inv[0]["id"], "Pix") is False)

service.change_plan(cid, basic["id"] + 1, "yearly")
service.write_allowed_file()
check("upgrade para Profissional libera o 2º técnico", u2 in allowed())

db.rd_remove_user(u2, group)
service.write_allowed_file()
check("técnico removido perde o acesso", u2 not in allowed())

service.set_status(cid, "cancel")
check("cancelado -> técnicos bloqueados", sub_of(cid)["status"] == "canceled" and u1 not in allowed())

check("login do painel recusa senha errada", db.rd_admin_login("admin", "senha-errada") is None)
check("datas: período mensal iniciado em 31/01 vai até 27/02", service.cycle_end(service.parse_date("2027-01-31"), "monthly").isoformat() == "2027-02-27")

shutil.rmtree(T, ignore_errors=True)
print("\nTUDO OK" if not failures else f"\n{failures} FALHA(S)")
sys.exit(1 if failures else 0)

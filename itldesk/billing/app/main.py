"""ITLDesk Cobrança: painel de planos, clientes, técnicos e faturas (Mercado Pago)."""
import asyncio
import os
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config, db, mp, service, validate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("itldesk.web")
B = config.BASE
SESSION_COOKIE = "itldesk_billing"
SESSION_MAX_AGE = 12 * 3600


async def _job_loop():
    while True:
        try:
            await asyncio.to_thread(service.run_cycle)
        except Exception:  # noqa: BLE001
            log.exception("Erro na rotina de cobrança")
        await asyncio.sleep(config.JOB_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_app):
    if not config.SECRET_KEY:
        raise RuntimeError("Defina ITLDESK_JWT_KEY (ou BILLING_SECRET_KEY)")
    db.init()
    task = asyncio.create_task(_job_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount(f"{B}/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["money"] = service.money
templates.env.filters["brdate"] = service.br_date
templates.env.globals.update(
    B=B, STATUS=service.STATUS_LABELS, CYCLES=service.CYCLE_LABELS,
    mp_enabled=mp.enabled, mp_test=mp.is_test,
    SITE=config, TRIAL_DAYS=config.TRIAL_DAYS, GRACE_DAYS=config.GRACE_DAYS,
)
signer = URLSafeTimedSerializer(config.SECRET_KEY or "-", salt="itldesk-billing")


# ---------------------------------------------------------------- autenticação

_fails: dict = {}


def _too_many(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 600]
    _fails[ip] = recent
    return len(recent) >= 5


def current_user(request: Request):
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        return signer.loads(raw, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None


def render(request: Request, name: str, **ctx):
    ctx.setdefault("user", current_user(request))
    ctx.setdefault("flash", request.query_params.get("msg"))
    ctx.setdefault("error", request.query_params.get("erro"))
    return templates.TemplateResponse(request, name, ctx)


def go(path: str, msg: str = "", error: str = ""):
    from urllib.parse import urlencode
    q = {k: v for k, v in (("msg", msg), ("erro", error)) if v}
    return RedirectResponse(f"{B}{path}" + (f"?{urlencode(q)}" if q else ""), status_code=303)


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    public = (
        path in (f"{B}/login", f"{B}/webhooks/mercadopago", f"{B}/saude")
        or path.startswith(f"{B}/static/")
        or path.startswith(f"{B}/fatura/")
        or not path.startswith(B)
    )
    if not public and not current_user(request):
        return RedirectResponse(f"{B}/login", status_code=303)
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.get(B, include_in_schema=False)
def root_redirect():
    return RedirectResponse(f"{B}/", status_code=303)


@app.get(f"{B}/saude")
def health():
    return {"ok": True}


@app.get(f"{B}/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html")


@app.post(f"{B}/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if _too_many(ip):
        return go("/login", error="Muitas tentativas. Aguarde 10 minutos.")
    user = db.rd_admin_login(username.strip(), password)
    if not user:
        _fails.setdefault(ip, []).append(time.time())
        return go("/login", error="Usuário ou senha inválidos (use um administrador do painel).")
    resp = go("/")
    resp.set_cookie(
        SESSION_COOKIE, signer.dumps(user), max_age=SESSION_MAX_AGE,
        httponly=True, secure=config.PUBLIC_URL.startswith("https"), samesite="lax", path=B,
    )
    return resp


@app.post(f"{B}/sair")
def logout():
    resp = go("/login")
    resp.delete_cookie(SESSION_COOKIE, path=B)
    return resp


def _who(request: Request) -> str:
    u = current_user(request) or {}
    return u.get("username", "")


# ---------------------------------------------------------------- painel

@app.get(f"{B}/", response_class=HTMLResponse)
def dashboard(request: Request):
    with db.billing() as b:
        counts = {r["status"]: r["n"] for r in b.execute("SELECT status, COUNT(*) n FROM subscriptions GROUP BY status")}
        mrr = b.execute(
            "SELECT COALESCE(SUM(CASE s.cycle WHEN 'yearly' THEN p.price_yearly / 12.0 ELSE p.price_monthly END), 0)"
            " FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.status IN ('active','past_due')"
        ).fetchone()[0]
        open_total = b.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'pending'").fetchone()[0]
        month = date.today().strftime("%Y-%m")
        received = b.execute(
            "SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status = 'paid' AND substr(paid_at,1,7) = ?", (month,)
        ).fetchone()[0]
        attention = b.execute(
            "SELECT c.id, c.name, s.status, s.period_end FROM subscriptions s JOIN customers c ON c.id = s.customer_id"
            " WHERE s.status IN ('past_due','blocked','pending') ORDER BY s.period_end"
        ).fetchall()
        events = b.execute(
            "SELECT e.*, c.name FROM events e LEFT JOIN customers c ON c.id = e.customer_id ORDER BY e.id DESC LIMIT 15"
        ).fetchall()
    return render(
        request, "dashboard.html", counts=counts, mrr=int(mrr), open_total=open_total,
        received=received, attention=attention, events=events,
    )


# ---------------------------------------------------------------- planos

@app.get(f"{B}/planos", response_class=HTMLResponse)
def plans_page(request: Request):
    with db.billing() as b:
        plans = b.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM subscriptions s WHERE s.plan_id = p.id AND s.status != 'canceled') subs"
            " FROM plans p ORDER BY p.active DESC, p.sort, p.id"
        ).fetchall()
    return render(request, "plans.html", plans=plans)


def _cents(value: str) -> int:
    v = value.strip().replace("R$", "").replace(" ", "")
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    return int(round(float(v) * 100))


@app.post(f"{B}/planos")
def save_plan(
    request: Request,
    plan_id: str = Form(""), name: str = Form(...), description: str = Form(""),
    max_technicians: int = Form(...), max_devices: str = Form(""),
    price_monthly: str = Form(...), price_yearly: str = Form(...),
    sort: int = Form(0), active: str = Form(""),
):
    try:
        values = (
            name.strip(), description.strip(), max(1, max_technicians),
            int(max_devices) if max_devices.strip() else None,
            _cents(price_monthly), _cents(price_yearly), 1 if active else 0, sort,
        )
    except ValueError:
        return go("/planos", error="Valores inválidos. Use números como 49,90.")
    with db.billing() as b:
        if plan_id:
            b.execute(
                "UPDATE plans SET name=?, description=?, max_technicians=?, max_devices=?, price_monthly=?,"
                " price_yearly=?, active=?, sort=? WHERE id=?", (*values, int(plan_id)),
            )
        else:
            b.execute(
                "INSERT INTO plans (name, description, max_technicians, max_devices, price_monthly, price_yearly,"
                " active, sort) VALUES (?,?,?,?,?,?,?,?)", values,
            )
    service.write_allowed_file()
    return go("/planos", msg="Plano salvo. Novos valores valem a partir das próximas faturas.")


# ---------------------------------------------------------------- clientes

@app.get(f"{B}/clientes", response_class=HTMLResponse)
def customers_page(request: Request, q: str = ""):
    with db.billing() as b:
        sql = (
            "SELECT c.*, s.status, s.cycle, s.period_end, p.name plan_name, p.max_technicians"
            " FROM customers c JOIN subscriptions s ON s.customer_id = c.id JOIN plans p ON p.id = s.plan_id"
        )
        args = ()
        if q.strip():
            sql += " WHERE c.name LIKE ? OR c.document LIKE ? OR c.email LIKE ?"
            args = (f"%{q.strip()}%",) * 3
        customers = b.execute(sql + " ORDER BY c.name", args).fetchall()
        plans = b.execute("SELECT * FROM plans WHERE active = 1 ORDER BY sort, id").fetchall()
    return render(request, "customers.html", customers=customers, plans=plans, q=q, trial_days=config.TRIAL_DAYS)


@app.post(f"{B}/clientes")
def new_customer(
    request: Request,
    name: str = Form(...), document: str = Form(""), email: str = Form(""), phone: str = Form(""),
    notes: str = Form(""), plan_id: int = Form(...), cycle: str = Form("monthly"), start_mode: str = Form("trial"),
):
    if not name.strip():
        return go("/clientes", error="Informe o nome do cliente.")
    if cycle not in service.CYCLE_LABELS or start_mode not in ("trial", "pending", "paid"):
        return go("/clientes", error="Opção inválida.")
    cid = service.create_customer(
        name.strip(), document.strip(), email.strip(), phone.strip(), notes.strip(),
        plan_id, cycle, start_mode, by=_who(request),
    )
    return go(f"/clientes/{cid}", msg="Cliente cadastrado. Agora crie os técnicos.")


def _customer(b, cid: int):
    return b.execute(
        "SELECT c.*, s.id sub_id, s.status, s.cycle, s.period_end, s.plan_id, p.name plan_name,"
        " p.max_technicians, p.max_devices FROM customers c JOIN subscriptions s ON s.customer_id = c.id"
        " JOIN plans p ON p.id = s.plan_id WHERE c.id = ?", (cid,),
    ).fetchone()


@app.get(f"{B}/clientes/{{cid}}", response_class=HTMLResponse)
def customer_page(request: Request, cid: int):
    with db.billing() as b:
        c = _customer(b, cid)
        if not c:
            return go("/clientes", error="Cliente não encontrado.")
        invoices = b.execute(
            "SELECT * FROM invoices WHERE subscription_id = ? ORDER BY id DESC", (c["sub_id"],)
        ).fetchall()
        plans = b.execute("SELECT * FROM plans WHERE active = 1 OR id = ? ORDER BY sort, id", (c["plan_id"],)).fetchall()
        events = b.execute("SELECT * FROM events WHERE customer_id = ? ORDER BY id DESC LIMIT 30", (cid,)).fetchall()
    techs = db.rd_group_users(c["rd_group_id"])
    devices = db.rd_device_count([t["id"] for t in techs])
    allowed = set(service.allowed_user_ids())
    return render(
        request, "customer.html", c=c, invoices=invoices, plans=plans, events=events, techs=techs,
        devices=devices, allowed=allowed, public_url=service.public_invoice_url,
    )


@app.post(f"{B}/clientes/{{cid}}/dados")
def update_customer(
    request: Request, cid: int, name: str = Form(...), document: str = Form(""), email: str = Form(""),
    phone: str = Form(""), notes: str = Form(""),
):
    with db.billing() as b:
        c = _customer(b, cid)
        if not c:
            return go("/clientes", error="Cliente não encontrado.")
        b.execute(
            "UPDATE customers SET name=?, document=?, email=?, phone=?, notes=? WHERE id=?",
            (name.strip(), document.strip(), email.strip(), phone.strip(), notes.strip(), cid),
        )
    if c["rd_group_id"]:
        db.rd_rename_group(c["rd_group_id"], f"Cliente: {name.strip()}")
    return go(f"/clientes/{cid}", msg="Dados salvos.")


@app.post(f"{B}/clientes/{{cid}}/plano")
def customer_plan(request: Request, cid: int, plan_id: int = Form(...), cycle: str = Form(...)):
    if cycle not in service.CYCLE_LABELS:
        return go(f"/clientes/{cid}", error="Ciclo inválido.")
    service.change_plan(cid, plan_id, cycle, by=_who(request))
    return go(f"/clientes/{cid}", msg="Plano alterado. Faturas em aberto foram refeitas com o novo valor.")


@app.post(f"{B}/clientes/{{cid}}/status")
def customer_status(request: Request, cid: int, action: str = Form(...)):
    if action not in ("cancel", "reactivate"):
        return go(f"/clientes/{cid}", error="Ação inválida.")
    service.set_status(cid, action, by=_who(request))
    return go(f"/clientes/{cid}", msg="Assinatura atualizada.")


@app.post(f"{B}/clientes/{{cid}}/vencimento")
def customer_due(request: Request, cid: int, period_end: str = Form(...)):
    try:
        new_end = date.fromisoformat(period_end)
    except ValueError:
        return go(f"/clientes/{cid}", error="Data inválida.")
    service.set_period_end(cid, new_end, by=_who(request))
    return go(f"/clientes/{cid}", msg="Vencimento ajustado.")


USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.@-]{3,64}$")


@app.post(f"{B}/clientes/{{cid}}/tecnicos")
def add_technician(
    request: Request, cid: int, username: str = Form(...), password: str = Form(...),
    nickname: str = Form(""), email: str = Form(""),
):
    with db.billing() as b:
        c = _customer(b, cid)
    if not c:
        return go("/clientes", error="Cliente não encontrado.")
    username = username.strip()
    if not USERNAME_RE.match(username):
        return go(f"/clientes/{cid}", error="Usuário inválido: use 3 a 64 letras, números, ponto, hífen ou @.")
    if len(password) < 8:
        return go(f"/clientes/{cid}", error="A senha precisa ter pelo menos 8 caracteres.")
    active = [t for t in db.rd_group_users(c["rd_group_id"]) if t["status"] == 1]
    if len(active) >= c["max_technicians"]:
        return go(f"/clientes/{cid}", error=f"O plano {c['plan_name']} permite {c['max_technicians']} técnico(s). Mude o plano para adicionar mais.")
    if db.rd_username_exists(username):
        return go(f"/clientes/{cid}", error="Esse usuário já existe no ITLDesk.")
    db.rd_create_user(username, password, nickname.strip() or username, email.strip(), c["rd_group_id"])
    with db.billing() as b:
        db.log_event(b, cid, f"Técnico {username} criado por {_who(request)}")
    service.write_allowed_file()
    return go(f"/clientes/{cid}", msg=f"Técnico {username} criado. Ele já pode entrar no app ITLDesk.")


@app.post(f"{B}/clientes/{{cid}}/tecnicos/{{uid}}/senha")
def tech_password(request: Request, cid: int, uid: int, password: str = Form(...)):
    if len(password) < 8:
        return go(f"/clientes/{cid}", error="A senha precisa ter pelo menos 8 caracteres.")
    with db.billing() as b:
        c = _customer(b, cid)
    if not c:
        return go("/clientes", error="Cliente não encontrado.")
    db.rd_set_password(uid, c["rd_group_id"], password)
    return go(f"/clientes/{cid}", msg="Senha alterada. O técnico precisa entrar de novo no app.")


@app.post(f"{B}/clientes/{{cid}}/tecnicos/{{uid}}/remover")
def tech_remove(request: Request, cid: int, uid: int):
    with db.billing() as b:
        c = _customer(b, cid)
        if not c:
            return go("/clientes", error="Cliente não encontrado.")
        db.rd_remove_user(uid, c["rd_group_id"])
        db.log_event(b, cid, f"Técnico #{uid} removido por {_who(request)}")
    service.write_allowed_file()
    return go(f"/clientes/{cid}", msg="Técnico removido e desconectado.")


# ---------------------------------------------------------------- faturas

@app.get(f"{B}/faturas", response_class=HTMLResponse)
def invoices_page(request: Request, status: str = "pending"):
    with db.billing() as b:
        sql = (
            "SELECT i.*, c.name customer, c.id customer_id FROM invoices i JOIN subscriptions s ON s.id = i.subscription_id"
            " JOIN customers c ON c.id = s.customer_id"
        )
        args = ()
        if status in ("pending", "paid", "canceled"):
            sql += " WHERE i.status = ?"
            args = (status,)
        invoices = b.execute(sql + " ORDER BY i.due_date DESC, i.id DESC LIMIT 300", args).fetchall()
    return render(request, "invoices.html", invoices=invoices, status=status, public_url=service.public_invoice_url)


@app.post(f"{B}/faturas/{{iid}}/pagar")
def invoice_paid(request: Request, iid: int, method: str = Form("Manual"), back: str = Form("/faturas")):
    ok = service.mark_paid(iid, method.strip() or "Manual", by=_who(request))
    if not back.startswith("/") or back.startswith("//"):
        back = "/faturas"
    return go(back, msg="Pagamento registrado." if ok else "", error="" if ok else "Fatura já paga ou inexistente.")


@app.post(f"{B}/faturas/{{iid}}/link")
def invoice_link(request: Request, iid: int, back: str = Form("/faturas")):
    url = service.ensure_payment_link(iid)
    if not back.startswith("/") or back.startswith("//"):
        back = "/faturas"
    if url:
        return go(back, msg="Link de pagamento gerado.")
    return go(back, error="Não foi possível gerar o link (confira MP_ACCESS_TOKEN no servidor).")


@app.post(f"{B}/rotina")
def run_now(request: Request):
    service.run_cycle()
    return go("/", msg="Rotina executada: status, faturas e pagamentos atualizados.")


# ---------------------------------------------------------------- área pública

@app.get(f"{B}/fatura/{{token}}", response_class=HTMLResponse)
def public_invoice(request: Request, token: str):
    with db.billing() as b:
        inv = b.execute(
            "SELECT i.*, c.name customer, s.status sub_status FROM invoices i"
            " JOIN subscriptions s ON s.id = i.subscription_id JOIN customers c ON c.id = s.customer_id"
            " WHERE i.public_token = ?", (token,),
        ).fetchone()
    if not inv:
        return HTMLResponse("Fatura não encontrada.", status_code=404)
    pay_url = inv["payment_url"]
    if inv["status"] == "pending" and not pay_url:
        pay_url = service.ensure_payment_link(inv["id"])
    # volta do checkout: confere o pagamento na hora, sem esperar o webhook
    pid = request.query_params.get("payment_id") or request.query_params.get("collection_id")
    if inv["status"] == "pending" and pid and pid.isdigit() and mp.enabled():
        try:
            if service.check_mp_payment(pid):
                return RedirectResponse(f"{B}/fatura/{token}", status_code=303)
        except Exception as e:  # noqa: BLE001
            log.warning("Erro ao conferir pagamento %s: %s", pid, e)
    return render(request, "public_invoice.html", inv=inv, pay_url=pay_url, user=None)


@app.post(f"{B}/webhooks/mercadopago")
async def mp_webhook(request: Request):
    params = request.query_params
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    kind = body.get("type") or body.get("topic") or params.get("type") or params.get("topic") or ""
    data_id = str((body.get("data") or {}).get("id") or params.get("data.id") or params.get("id") or "")
    if not mp.valid_signature(request.headers.get("x-signature", ""), request.headers.get("x-request-id", ""), params.get("data.id", data_id)):
        log.warning("Webhook Mercado Pago com assinatura inválida (id=%s)", data_id)
        return JSONResponse({"ok": False}, status_code=401)
    if kind == "payment" and data_id.isdigit() and mp.enabled():
        try:
            paid = await asyncio.to_thread(service.check_mp_payment, data_id)
            log.info("Webhook Mercado Pago: pagamento %s %s", data_id, "aplicado" if paid else "ignorado")
        except Exception:  # noqa: BLE001
            log.exception("Erro ao processar webhook %s", data_id)
            return JSONResponse({"ok": False}, status_code=500)
    return {"ok": True}


# ---------------------------------------------------------------- site de vendas (público)

def _active_plans():
    with db.billing() as b:
        return b.execute("SELECT * FROM plans WHERE active = 1 ORDER BY sort, id").fetchall()


def _downloads():
    files = []
    try:
        names = sorted(os.listdir(config.DOWNLOADS_DIR))
    except OSError:
        return files
    for name in names:
        path = os.path.join(config.DOWNLOADS_DIR, name)
        if os.path.isfile(path) and name.lower().endswith((".exe", ".msi")):
            kind = "Instalador MSI (empresas / GPO)" if name.lower().endswith(".msi") else "Instalador para Windows"
            files.append({"name": name, "size_mb": round(os.path.getsize(path) / 1048576), "kind": kind})
    # .exe primeiro: é o que a maioria deve baixar
    files.sort(key=lambda f: not f["name"].lower().endswith(".exe"))
    return files


@app.get("/", response_class=HTMLResponse)
def site_home(request: Request):
    return render(request, "site/home.html", plans=_active_plans(), downloads=_downloads(), user=None)


@app.get("/termos", response_class=HTMLResponse)
def site_terms(request: Request):
    return render(request, "site/terms.html", user=None)


@app.get("/download/{name}")
def site_download(name: str):
    if name not in {f["name"] for f in _downloads()}:
        return HTMLResponse("Arquivo não encontrado.", status_code=404)
    return FileResponse(os.path.join(config.DOWNLOADS_DIR, name), filename=name,
                        media_type="application/octet-stream")


def _signup_form(request: Request, values: dict, error: str = ""):
    plans = _active_plans()
    return render(request, "site/signup.html", plans=plans, v=values, error=error, user=None,
                  signup_mode=config.SIGNUP_MODE)


@app.get("/assinar", response_class=HTMLResponse)
def site_signup(request: Request, plano: str = "", ciclo: str = "monthly"):
    return _signup_form(request, {"plan_id": plano, "cycle": ciclo if ciclo in service.CYCLE_LABELS else "monthly"})


_signups: dict = {}


@app.post("/assinar", response_class=HTMLResponse)
def site_signup_post(
    request: Request,
    name: str = Form(""), document: str = Form(""), email: str = Form(""), phone: str = Form(""),
    username: str = Form(""), password: str = Form(""), password2: str = Form(""),
    plan_id: str = Form(""), cycle: str = Form("monthly"), terms: str = Form(""), website: str = Form(""),
):
    values = {"name": name.strip(), "document": document.strip(), "email": email.strip().lower(),
              "phone": phone.strip(), "username": username.strip(), "plan_id": plan_id, "cycle": cycle}
    if website:  # campo invisível: robôs preenchem
        return _signup_form(request, values, "Não foi possível concluir o cadastro.")
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    now = time.time()
    recent = [t for t in _signups.get(ip, []) if now - t < 86400]
    _signups[ip] = recent
    if len(recent) >= config.SIGNUPS_PER_IP_PER_DAY:
        return _signup_form(request, values, "Muitos cadastros feitos desta rede hoje. Fale com o suporte.")

    doc = validate.document(document)
    tel = validate.phone(phone)
    with db.billing() as b:
        plan = b.execute("SELECT * FROM plans WHERE id = ? AND active = 1", (plan_id if plan_id.isdigit() else 0,)).fetchone()
        dup = b.execute(
            "SELECT 1 FROM customers WHERE lower(email) = ? OR (document != '' AND document = ?)",
            (values["email"], doc or "-"),
        ).fetchone()
    errors = []
    if len(values["name"]) < 3:
        errors.append("Informe seu nome ou o nome da empresa.")
    if not doc:
        errors.append("CPF ou CNPJ inválido.")
    if not validate.EMAIL_RE.match(values["email"]):
        errors.append("E-mail inválido.")
    if not tel:
        errors.append("Telefone inválido (use DDD + número).")
    if not plan:
        errors.append("Escolha um plano.")
    if cycle not in service.CYCLE_LABELS:
        errors.append("Escolha mensal ou anual.")
    if not validate.USERNAME_RE.match(values["username"]):
        errors.append("Usuário de acesso: 3 a 64 letras, números, ponto, hífen ou @, sem espaços.")
    elif db.rd_username_exists(values["username"]):
        errors.append("Esse usuário de acesso já existe. Escolha outro.")
    if len(password) < 8:
        errors.append("A senha precisa ter pelo menos 8 caracteres.")
    elif password != password2:
        errors.append("As senhas não conferem.")
    if not terms:
        errors.append("É preciso aceitar os termos de uso.")
    if dup and not errors:
        errors.append("Já existe uma conta com esse e-mail ou documento. Fale com o suporte para reativar.")
    if errors:
        return _signup_form(request, values, " ".join(errors))

    mode = "trial" if config.SIGNUP_MODE == "trial" else "pending"
    cid = service.create_customer(values["name"], doc, values["email"], tel, "Cadastro pelo site", plan["id"], cycle, mode, by="site")
    with db.billing() as b:
        group = b.execute("SELECT rd_group_id FROM customers WHERE id = ?", (cid,)).fetchone()[0]
    db.rd_create_user(values["username"], password, values["name"], values["email"], group)
    with db.billing() as b:
        db.log_event(b, cid, f"Técnico {values['username']} criado pelo cadastro do site")
        inv = b.execute(
            "SELECT i.public_token FROM invoices i JOIN subscriptions s ON s.id = i.subscription_id"
            " WHERE s.customer_id = ? AND i.status = 'pending' ORDER BY i.id DESC", (cid,),
        ).fetchone()
    service.write_allowed_file()
    _signups[ip].append(now)
    log.info("Novo cadastro pelo site: cliente %s (%s)", cid, values["email"])
    return RedirectResponse(f"/assinar/pronto/{inv['public_token']}", status_code=303)


@app.get("/assinar/pronto/{token}", response_class=HTMLResponse)
def site_signup_done(request: Request, token: str):
    with db.billing() as b:
        inv = b.execute(
            "SELECT i.*, c.id customer_id, c.name customer, c.rd_group_id, s.status sub_status, s.period_end,"
            " p.name plan_name FROM invoices i JOIN subscriptions s ON s.id = i.subscription_id"
            " JOIN customers c ON c.id = s.customer_id JOIN plans p ON p.id = s.plan_id WHERE i.public_token = ?",
            (token,),
        ).fetchone()
    if not inv:
        return HTMLResponse("Cadastro não encontrado.", status_code=404)
    techs = db.rd_group_users(inv["rd_group_id"])
    return render(request, "site/done.html", inv=inv, username=techs[0]["username"] if techs else "",
                  downloads=_downloads(), invoice_url=service.public_invoice_url(token), user=None)

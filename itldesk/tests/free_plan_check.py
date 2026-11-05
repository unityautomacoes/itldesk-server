#!/usr/bin/env python3
"""Testa o plano gratuito no hbbs/hbbr: sessão só pelo relay, 1 por vez, corte por tempo.

Uso (no servidor, com o hbbr rodando com ITLDESK_FREE_SESSION_SECS curto, ex. 20):
    python3 free_plan_check.py /opt/itldesk/itldesk [segundos_do_corte]
Inclui um técnico fictício (id 990001) como gratuito na lista de liberados; o billing
regrava a lista na rotina seguinte (até 5 min), removendo-o.
"""
import json
import os
import socket
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hbbs_login_check import field_bytes, frame, make_jwt, parse, punch  # noqa: E402

FREE_UID = 990001


def request_relay_hbbs(token: str, relay_uuid: str) -> bool:
    """RequestRelay ao hbbs (como o técnico faz quando cai para o relay). True = aceito."""
    rr = field_bytes(1, b"999999999") + field_bytes(2, relay_uuid.encode()) + field_bytes(8, token.encode())
    with socket.create_connection(("127.0.0.1", 21116), timeout=5) as s:
        s.sendall(frame(field_bytes(18, rr)))
        s.settimeout(2)
        try:
            data = s.recv(100)
            return data != b""  # hbbs fecha a conexão quando recusa
        except socket.timeout:
            return True  # aceito: conexão continua aberta


def relay_leg(pub: str, relay_uuid: str) -> socket.socket:
    rr = field_bytes(2, relay_uuid.encode()) + field_bytes(6, pub.encode())
    s = socket.create_connection(("127.0.0.1", 21117), timeout=5)
    s.sendall(frame(field_bytes(18, rr)))
    return s


def alive(s: socket.socket) -> bool:
    s.settimeout(0.5)
    try:
        return s.recv(1, socket.MSG_PEEK) != b""
    except socket.timeout:
        return True
    except OSError:
        return False


def active(base: str):
    try:
        return json.load(open(f"{base}/data/itldesk_free_active.json"))["user_ids"]
    except Exception:  # noqa: BLE001
        return None


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "/opt/itldesk/itldesk"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    env = dict(line.strip().split("=", 1) for line in open(f"{base}/.env") if "=" in line and not line.startswith("#"))
    pub = open(f"{base}/data/id_ed25519.pub").read().strip()
    token = make_jwt(FREE_UID, env["ITLDESK_JWT_KEY"])

    path = f"{base}/data/itldesk_allowed_users.json"
    allowed = json.load(open(path))
    allowed["user_ids"] = sorted(set(allowed["user_ids"]) | {FREE_UID})
    allowed["free_user_ids"] = sorted(set(allowed.get("free_user_ids", [])) | {FREE_UID})
    tmp = path + ".test"
    json.dump(allowed, open(tmp, "w"))
    os.replace(tmp, path)
    print("técnico gratuito de teste incluído; aguardando o hbbs recarregar a lista (16s)...")
    time.sleep(16)

    ok = True
    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"{'OK ' if cond else 'FALHOU'} {name}")

    check("conexão livre antes de qualquer sessão", "Plano gratuito" not in punch(pub, token))
    u1 = str(uuid.uuid4())
    check("relay autorizado pelo hbbs", request_relay_hbbs(token, u1))
    a, b = relay_leg(pub, u1), relay_leg(pub, u1)
    time.sleep(1.5)
    check("sessão aberta no relay", alive(a) and alive(b))
    check("hbbr marcou o técnico em sessão", active(base) == [FREE_UID])
    msg = punch(pub, token)
    check(f"2ª conexão recusada com aviso ({msg[:60]}...)", msg.startswith("Plano gratuito"))
    check("2º relay recusado pelo hbbs", not request_relay_hbbs(token, str(uuid.uuid4())))
    start = time.time()
    while time.time() - start < limit + 15 and alive(a):
        a.sendall(b"x")  # mantém tráfego como numa sessão real
        b.recv(4096)
        time.sleep(2)
    took = time.time() - start + 1.5
    check(f"sessão encerrada pelo tempo ({took:.0f}s, limite {limit}s)", not alive(a) and took < limit + 10)
    time.sleep(1)
    check("vaga liberada", active(base) == [])
    check("nova conexão liberada depois do corte", "Plano gratuito" not in punch(pub, token))
    print("RESULTADO:", "tudo certo" if ok else "há falhas")


if __name__ == "__main__":
    main()

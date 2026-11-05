#!/usr/bin/env python3
"""Testa o bloqueio de login/assinatura do hbbs ITLDesk enviando PunchHoleRequest direto na porta 21116.

Uso (no servidor):  python3 hbbs_login_check.py /opt/itldesk/itldesk
Lê a chave JWT do .env e a chave pública do servidor em data/id_ed25519.pub. Só usa a biblioteca padrão.
"""
import base64
import hashlib
import hmac
import json
import socket
import struct
import sys
import time


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field_bytes(num: int, data: bytes) -> bytes:
    return varint(num << 3 | 2) + varint(len(data)) + data


def frame(data: bytes) -> bytes:
    n = len(data)
    if n <= 0x3F:
        return bytes([n << 2]) + data
    if n <= 0x3FFF:
        return struct.pack("<H", (n << 2) | 1) + data
    h = (n << 2) | 2
    return struct.pack("<H", h & 0xFFFF) + bytes([h >> 16]) + data


def read_varint(buf: bytes, i: int):
    shift = n = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def parse(buf: bytes) -> dict:
    out, i = {}, 0
    while i < len(buf):
        key, i = read_varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = read_varint(buf, i)
        elif wt == 2:
            ln, i = read_varint(buf, i)
            v, i = buf[i:i + ln], i + ln
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        else:
            raise ValueError(f"wire type {wt}")
        out[num] = v
    return out


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(user_id: int, key: str, exp_in: int = 3600, sign_key: str = None) -> str:
    head = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64url(json.dumps({"user_id": user_id, "exp": int(time.time()) + exp_in}).encode())
    sig = hmac.new((sign_key or key).encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64url(sig)}"


def punch(licence_key: str, token: str, target: str = "999999999") -> str:
    ph = field_bytes(1, target.encode()) + field_bytes(3, licence_key.encode())
    if token:
        ph += field_bytes(5, token.encode())
    msg = field_bytes(8, ph)  # RendezvousMessage.punch_hole_request = 8
    with socket.create_connection(("127.0.0.1", 21116), timeout=5) as s:
        s.sendall(frame(msg))
        data = s.recv(65536)
    hl = (data[0] & 3) + 1
    n = int.from_bytes(data[:hl], "little") >> 2
    resp = parse(data[hl:hl + n])
    phr = parse(resp.get(11, b""))  # punch_hole_response = 11
    other = phr.get(7, b"").decode()  # other_failure
    failure = phr.get(3, 0)  # 0 = ID_NOT_EXIST
    return other or f"(passou na checagem de login; failure={failure})"


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "/opt/itldesk/itldesk"
    env = dict(
        line.strip().split("=", 1) for line in open(f"{base}/.env") if "=" in line and not line.startswith("#")
    )
    key = env["ITLDESK_JWT_KEY"]
    pub = open(f"{base}/data/id_ed25519.pub").read().strip()
    allowed = json.load(open(f"{base}/data/itldesk_allowed_users.json"))["user_ids"]
    blocked_uid = max(allowed + [0]) + 100000
    cases = [
        ("sem login", ""),
        ("token inválido", "abc.def.ghi"),
        ("token assinado com outra chave", make_jwt(allowed[0], key, sign_key="outra-chave")),
        ("token expirado", make_jwt(allowed[0], key, exp_in=-3600)),
        (f"técnico sem assinatura (id {blocked_uid})", make_jwt(blocked_uid, key)),
    ] + [(f"liberado (id {uid})", make_jwt(uid, key)) for uid in allowed]
    for name, token in cases:
        print(f"{name:42} -> {punch(pub, token)}")


if __name__ == "__main__":
    main()

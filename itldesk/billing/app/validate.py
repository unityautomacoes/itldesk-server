"""Validações do cadastro público."""
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.@-]{3,64}$")


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _cpf_ok(d: str) -> bool:
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (s * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def _cnpj_ok(d: str) -> bool:
    if len(d) != 14 or d == d[0] * 14:
        return False
    for n, weights in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        r = sum(int(d[i]) * weights[i] for i in range(n)) % 11
        if (0 if r < 2 else 11 - r) != int(d[n]):
            return False
    return True


def document(value: str):
    """Retorna o CPF/CNPJ formatado, ou None se inválido."""
    d = digits(value)
    if _cpf_ok(d):
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if _cnpj_ok(d):
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return None


def phone(value: str):
    d = digits(value)
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) not in (10, 11):
        return None
    return f"({d[:2]}) {d[2:-4]}-{d[-4:]}"

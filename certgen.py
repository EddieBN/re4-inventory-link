"""Certificado HTTPS autoassinado gerado em Python puro (sem OpenSSL, sem dependências).

Os navegadores (Chrome, Firefox) só liberam a leitura de controles (Gamepad API) em páginas seguras;
numa rede local isso exige HTTPS. O certificado é criado uma vez em certs/ e reaproveitado.
O navegador mostra um aviso na primeira visita (certificado não emitido por uma autoridade): basta aceitar.
"""
import datetime
import hashlib
import ipaddress
import os
import secrets

HERE = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(HERE, "certs")
CERT_FILE = os.path.join(CERT_DIR, "cert.pem")
KEY_FILE = os.path.join(CERT_DIR, "key.pem")


# ------------------------------------------------------------------ RSA
def _is_probable_prime(n, rounds=40):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits):
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(c):
            return c


def _rsa_key(bits=2048, e=65537):
    while True:
        p, q = _prime(bits // 2), _prime(bits // 2)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            n = p * q
            d = pow(e, -1, phi)
            return n, e, d, p, q


# ------------------------------------------------------------------ DER (ASN.1)
def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, content):
    return bytes([tag]) + _len(len(content)) + content


def _int(v):
    b = v.to_bytes((v.bit_length() + 8) // 8, "big") if v else b"\0"
    return _tlv(0x02, b)


def _seq(*items):
    return _tlv(0x30, b"".join(items))


def _oid(dotted):
    parts = [int(x) for x in dotted.split(".")]
    out = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        enc = [p & 0x7F]
        p >>= 7
        while p:
            enc.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(enc))
    return _tlv(0x06, out)


NULL = b"\x05\x00"
SHA256_RSA = _seq(_oid("1.2.840.113549.1.1.11"), NULL)


def _name(cn):
    return _seq(_tlv(0x31, _seq(_oid("2.5.4.3"), _tlv(0x0C, cn.encode()))))


def _time(t):
    if t.year < 2050:
        return _tlv(0x17, t.strftime("%y%m%d%H%M%SZ").encode())
    return _tlv(0x18, t.strftime("%Y%m%d%H%M%SZ").encode())


def _pem(label, der):
    import base64
    b = base64.b64encode(der).decode()
    lines = "\n".join(b[i:i + 64] for i in range(0, len(b), 64))
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----\n"


def generate(ips, names=("localhost",), cn="RE4 Inventory Link"):
    n, e, d, p, q = _rsa_key()
    now = datetime.datetime.now(datetime.timezone.utc)
    san = []
    for nm in names:
        san.append(_tlv(0x82, nm.encode()))                       # dNSName
    for ip in ips:
        san.append(_tlv(0x87, ipaddress.ip_address(ip).packed))  # iPAddress
    exts = _tlv(0xA3, _seq(
        _seq(_oid("2.5.29.17"), _tlv(0x04, _seq(*san))),                       # subjectAltName
        _seq(_oid("2.5.29.19"), _tlv(0x04, _seq())),                           # basicConstraints CA:FALSE
        _seq(_oid("2.5.29.37"), _tlv(0x04, _seq(_oid("1.3.6.1.5.5.7.3.1")))),  # extKeyUsage serverAuth
    ))
    spki = _seq(_seq(_oid("1.2.840.113549.1.1.1"), NULL), _tlv(0x03, b"\0" + _seq(_int(n), _int(e))))
    tbs = _seq(
        _tlv(0xA0, _int(2)),                                    # v3
        _int(secrets.randbits(63) | 1),                          # serial
        SHA256_RSA,
        _name(cn),
        _seq(_time(now - datetime.timedelta(days=1)), _time(now + datetime.timedelta(days=3650))),
        _name(cn),
        spki,
        exts,
    )
    # assinatura PKCS#1 v1.5 com SHA-256
    digest_info = _seq(_seq(_oid("2.16.840.1.101.3.4.2.1"), NULL), _tlv(0x04, hashlib.sha256(tbs).digest()))
    k = (n.bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - 3 - len(digest_info)) + b"\x00" + digest_info
    sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
    cert = _seq(tbs, SHA256_RSA, _tlv(0x03, b"\0" + sig))
    key = _seq(_int(0), _int(n), _int(e), _int(d), _int(p), _int(q),
               _int(d % (p - 1)), _int(d % (q - 1)), _int(pow(q, -1, p)))
    return _pem("CERTIFICATE", cert), _pem("RSA PRIVATE KEY", key)


def ensure(ips):
    """Garante cert.pem/key.pem em certs/ (gera na primeira vez). Retorna (cert, key)."""
    if not (os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE)):
        os.makedirs(CERT_DIR, exist_ok=True)
        cert, key = generate(list(ips) + ["127.0.0.1"])
        with open(KEY_FILE, "w") as f:
            f.write(key)
        with open(CERT_FILE, "w") as f:
            f.write(cert)
    return CERT_FILE, KEY_FILE

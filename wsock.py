"""WebSocket mínimo (RFC 6455) sobre o BaseHTTPRequestHandler — só a biblioteca padrão.

Usado para o controle pelo navegador: a página manda pacotes binários RE4P (o mesmo formato do Switch)
e recebe a vibração (RE4R).
"""
import base64
import hashlib
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept(handler):
    """Faz o handshake. Retorna True se a conexão virou WebSocket."""
    key = handler.headers.get("Sec-WebSocket-Key")
    if not key or "websocket" not in (handler.headers.get("Upgrade") or "").lower():
        return False
    token = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", token)
    handler.end_headers()
    handler.wfile.flush()
    return True


def _read_exact(f, n):
    b = b""
    while len(b) < n:
        chunk = f.read(n - len(b))
        if not chunk:
            raise ConnectionError("fechado")
        b += chunk
    return b


def recv(f):
    """Lê um frame. Retorna (opcode, payload). Frames de controle são devolvidos também."""
    b0, b1 = _read_exact(f, 2)
    op, masked, n = b0 & 0x0F, b1 & 0x80, b1 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _read_exact(f, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _read_exact(f, 8))[0]
    if n > 1 << 16:
        raise ConnectionError("frame grande demais")
    mask = _read_exact(f, 4) if masked else b"\0\0\0\0"
    data = bytearray(_read_exact(f, n))
    for i in range(n):
        data[i] ^= mask[i & 3]
    return op, bytes(data)


def send(f, payload, op=0x2):
    n = len(payload)
    head = bytes([0x80 | op])
    if n < 126:
        head += bytes([n])
    elif n < 1 << 16:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    f.write(head + payload)
    f.flush()

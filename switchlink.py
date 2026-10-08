"""Ponte com o homebrew do Switch.

* Controle: o Switch manda o estado dos botões/analógicos por UDP (porta 8045) ~60x por segundo;
  cada pacote vira o controle virtual XInput do jogo (game.set_pad). Se os pacotes param por
  PAD_TIMEOUT segundos, o controle é solto (nada fica "preso" apertado). A vibração que o jogo pede
  volta na resposta.
* Maleta: estado em texto simples (uma linha por item, campos separados por TAB), fácil de ler em C.

Pacote do Switch (little-endian, 20 bytes):  "RE4P" u32 seq | u16 botões XInput | u8 LT | u8 RT |
                                              i16 LX | i16 LY | i16 RX | i16 RY
Resposta (8 bytes):                           "RE4R" u16 vibração esquerda | u16 vibração direita
Descoberta: o Switch manda "RE4?" em broadcast para a porta 8045; o servidor responde "RE4!" u16 porta HTTP.
"""
import socket
import struct
import threading
import time

PAD_PORT = 8045
PAD_TIMEOUT = 0.4
PKT = struct.Struct("<4sIHBBhhhh")
REPLY = struct.Struct("<4sHH")


class PadReceiver:
    def __init__(self, game, http_port=8044):
        self.game = game
        self.http_port = http_port
        self.last = 0.0
        self.client = None
        self.active = False

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", PAD_PORT))
        sock.settimeout(0.1)
        while True:
            try:
                data, addr = sock.recvfrom(64)
            except socket.timeout:
                self._watchdog()
                continue
            except OSError:
                time.sleep(0.1)
                continue
            if data == b"RE4?":                      # descoberta automática do servidor na rede
                sock.sendto(b"RE4!" + struct.pack("<H", self.http_port), addr)
                print(f"[pad] Switch procurando o servidor ({addr[0]}) — respondido")
                continue
            if len(data) != PKT.size:
                continue
            magic, _seq, buttons, lt, rt, lx, ly, rx, ry = PKT.unpack(data)
            if magic != b"RE4P" or not self.game.connected():
                continue
            try:
                self.game.set_pad(buttons, lt, rt, lx, ly, rx, ry)
                left, right = self.game.rumble()
                sock.sendto(REPLY.pack(b"RE4R", left, right), addr)
            except OSError:
                continue
            if not self.active or self.client != addr[0]:
                print(f"[pad] controle do Switch conectado ({addr[0]})")
            self.last, self.client, self.active = time.time(), addr[0], True

    def _watchdog(self):
        if self.active and time.time() - self.last > PAD_TIMEOUT:
            self.game.release_pad()
            self.active = False
            print("[pad] controle do Switch desconectado")


def _clean(s):
    return str(s).replace("\t", " ").replace("\n", " ")


def text_state(st, version, icon_map):
    """Estado da maleta no formato de texto lido pelo homebrew."""
    lines = [f"V\t{version}"]
    if not st.get("connected"):
        lines.append(f"E\t{_clean(st.get('error', 'Jogo não conectado'))}")
        return "\n".join(lines) + "\n"
    lines.append(f"S\t1\t{int(bool(st.get('running')))}\t{st['caseW']}\t{st['caseH']}\t{st['caseLevel']}")
    for it in st["items"]:
        if it["type"] == 1:
            count = "-" if it["id"] in (13, 56) else it.get("ammo", 0)
        elif it["type"] in (2, 3) or it["num"] > 1:
            count = it["num"]
        else:
            count = "-"
        icon = icon_map.get(str(it["id"])) or icon_map.get(it["key"]) or icon_map.get(it["name"]) or "-"
        f = [it["slot"], it["id"], it["x"], it["y"], it["w"], it["h"], it["rot"], int(it["equipped"]), count,
             it["type"], it.get("ammo", 0), it.get("ammoMax") or 0, it.get("firepower", 0), it.get("firingSpeed", 0),
             it.get("reloadSpeed", 0), it.get("capacity", 0), it["num"], it["max"], icon.split("?")[0], _clean(it["name"])]
        lines.append("I\t" + "\t".join(str(x) for x in f))
    for o in st["others"]:
        lines.append(f"O\t{o['slot']}\t{o['type']}\t{o['num']}\t{_clean(o['name'])}")
    return "\n".join(lines) + "\n"

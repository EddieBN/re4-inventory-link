"""RE4 Inventory Link — servidor web (intranet) que espelha e controla o inventário.

Uso:  python server.py [--port 8044]
Depois abra http://<ip-do-pc>:8044 no celular (mesma rede Wi-Fi).
"""
import argparse
import atexit
import json
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from game import Game
import icons
import switchlink

HERE = os.path.dirname(os.path.abspath(__file__))
game = Game()
cond = threading.Condition()
snapshot = {"version": 0, "json": json.dumps({"connected": False, "error": "Iniciando..."})}


def publish(state):
    js = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    with cond:
        if js != snapshot["json"]:
            snapshot["json"] = js
            snapshot["version"] += 1
            cond.notify_all()


def full_state():
    st = game.state()
    st["running"] = game.running()
    return st


def poller():
    last_try = 0
    while True:
        try:
            if not game.connected():
                if time.time() - last_try > 2:
                    last_try = time.time()
                    try:
                        game.attach()
                        print(f"[+] Conectado ao bio4.exe (pid {game.p.pid}), hook em {game.data:#x}")
                    except Exception as e:  # jogo fechado ou ainda carregando
                        game.p = None
                        publish({"connected": False, "error": str(e)})
                time.sleep(0.25)
                continue
            publish(full_state())
        except Exception as e:
            traceback.print_exc()
            publish({"connected": False, "error": str(e)})
            time.sleep(1)
        time.sleep(0.1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "web", "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/manifest.webmanifest":
            with open(os.path.join(HERE, "web", "manifest.webmanifest"), "rb") as f:
                return self._send(200, f.read(), "application/manifest+json")
        if path == "/api/icons":
            return self._send(200, json.dumps(icons.icons(), ensure_ascii=False))
        if path.startswith("/icons/"):
            return self._file(path.lstrip("/"))
        if path == "/api/state":
            return self._send(200, snapshot["json"])
        if path == "/api/events":
            return self._events()
        if path == "/api/switch/state":
            return self._switch_state()
        self._send(404, '{"error":"not found"}')

    TYPES = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

    def _file(self, rel):
        from urllib.parse import unquote
        root = os.path.join(HERE, "web")
        full = os.path.normpath(os.path.join(root, unquote(rel)))
        ext = os.path.splitext(full)[1].lower()
        if not full.startswith(os.path.normpath(root) + os.sep) or ext not in self.TYPES or not os.path.isfile(full):
            return self._send(404, '{"error":"not found"}')
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", self.TYPES[ext])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=31536000, immutable")  # URL leva ?v=versão
        self.end_headers()
        self.wfile.write(data)

    def _switch_state(self):
        """Long-poll: responde quando a versão muda (ou após 8 s). ?v=<última versão vista>."""
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(self.path).query)
        seen = int(q.get("v", ["-1"])[0])
        with cond:
            if snapshot["version"] == seen:
                cond.wait(timeout=8)
            ver, js = snapshot["version"], snapshot["json"]
        body = switchlink.text_state(json.loads(js), ver, icons.icons())
        self._send(200, body, "text/plain; charset=utf-8")

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        seen = -1
        try:
            while True:
                with cond:
                    if snapshot["version"] == seen:
                        cond.wait(timeout=10)
                    ver, js = snapshot["version"], snapshot["json"]
                if ver != seen:
                    self.wfile.write(f"data: {js}\n\n".encode("utf-8"))
                    seen = ver
                else:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if not game.connected():
                raise ValueError("Jogo não conectado")
            slot = int(body.get("slot", -1))
            result = {"ok": True}
            if path == "/api/equip":
                result["pending"] = game.equip(slot) == "pending"
            elif path == "/api/move":
                game.move(slot, int(body["x"]), int(body["y"]), int(body.get("rot", 0)))
            elif path == "/api/count":
                game.set_count(slot, int(body["num"]))
            elif path == "/api/ammo":
                game.set_ammo(slot, int(body["ammo"]))
            elif path == "/api/use":
                result.update(game.use(slot))
            elif path == "/api/discard":
                game.discard(slot)
            else:
                return self._send(404, '{"error":"not found"}')
            publish(full_state())
            self._send(200, json.dumps(result))
        except (ValueError, KeyError, TimeoutError) as e:
            self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        except Exception as e:
            traceback.print_exc()
            self._send(500, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return  # navegador fechou a conexão — normal
        super().handle_error(request, client_address)


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8044)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    atexit.register(game.unhook)
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=switchlink.PadReceiver(game).run, daemon=True).start()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    srv = Server((args.host, args.port), Handler)
    print("=" * 56)
    print(" RE4 Inventory Link — abra no celular (mesma rede):")
    for ip in lan_ips():
        print(f"   http://{ip}:{args.port}")
    print(f"   (neste PC: http://localhost:{args.port})")
    print(f" Switch: abra o RE4 Inventory e digite o IP acima (controle via UDP {switchlink.PAD_PORT})")
    print(" Ctrl+C para sair (o hook é removido do jogo).")
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        game.unhook()
        print("Hook removido. Até mais, estrangeiro!")


if __name__ == "__main__":
    main()

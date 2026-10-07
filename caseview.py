"""Geometria da maleta: projeta a grade de células sobre a imagem de cada tamanho de maleta.

Cada célula mede 100 unidades no mundo 3D (mundo = M_board · (100·u, −100·v, 0)) e a câmera do
inventário foi ajustada uma vez (calib.json, erro de 0,2 px). A matriz da maleta (M_board) de cada
tamanho é gravada automaticamente quando a maleta fica parada na tela do jogo; com ela, qualquer
screenshot 16:9 daquele tamanho recebe a grade exata.
"""
import json
import os
import threading
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
CALIB_FILE = os.path.join(HERE, "calib.json")
CASES_DIR = os.path.join(HERE, "web", "cases")
ICONS_DIR = os.path.join(HERE, "web", "icons")
REF_W = 1920
CASE_NAMES = ["S", "M", "L", "XL"]
CASE_SIZES = [(10, 6), (11, 7), (12, 8), (15, 8)]
IMG_EXT = (".webp", ".png", ".jpg", ".jpeg")


def _rot(rx, ry, rz):
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def board_homography(cam, M, img_w=REF_W):
    """Homografia (u, v, 1) em células -> pixel da imagem (16:9, largura img_w)."""
    M = np.asarray(M, dtype=float).reshape(3, 4)
    A = np.column_stack([100 * M[:, 0], -100 * M[:, 1], M[:, 3] - np.array(cam["pos"])])
    K = np.array([[cam["f"], 0, -cam["cx"]], [0, -cam["f"], -cam["cy"]], [0, 0, -1.0]])
    H = np.diag([img_w / REF_W, img_w / REF_W, 1.0]) @ K @ _rot(*cam["rot"]) @ A
    return H / H[2, 2]


class Cases:
    def __init__(self):
        self.lock = threading.Lock()
        with open(CALIB_FILE, encoding="utf-8") as f:
            self.calib = json.load(f)
        self.calib.setdefault("boards", {})
        self._pending = None          # (nível, matriz, desde)
        self.version = 0

    # ---- grava a matriz da maleta quando ela fica parada (a maleta desliza ao abrir o inventário)
    def observe(self, level, matrix):
        now = time.time()
        if not self._pending or self._pending[0] != level or self._pending[1] != matrix:
            self._pending = (level, matrix, now)
            return
        if now - self._pending[2] < 1.0 or self.calib["boards"].get(str(level)) == matrix:
            return
        with self.lock:
            self.calib["boards"][str(level)] = matrix
            tmp = CALIB_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.calib, f, indent=1)
            os.replace(tmp, CALIB_FILE)
            self.version += 1
        print(f"[maleta] matriz da maleta {CASE_NAMES[level]} registrada")

    def _image(self, name):
        for ext in IMG_EXT:
            p = os.path.join(CASES_DIR, name + ext)
            if os.path.exists(p):
                return p
        return None

    def info(self):
        out = []
        for lvl, (w, h) in enumerate(CASE_SIZES):
            name = CASE_NAMES[lvl]
            img = self._image(name)
            entry = {"level": lvl, "name": name, "size": [w, h], "image": None, "H": None}
            M = self.calib["boards"].get(str(lvl))
            if img:
                with Image.open(img) as im:
                    iw, ih = im.size
                mtime = int(os.path.getmtime(img))
                entry["image"] = {"url": f"/cases/{os.path.basename(img)}?v={mtime}", "w": iw, "h": ih}
                if M:
                    entry["H"] = board_homography(self.calib["camera"], M, iw).tolist()
            out.append(entry)
        icons = {}
        if os.path.isdir(ICONS_DIR):
            for fn in os.listdir(ICONS_DIR):
                base, ext = os.path.splitext(fn)
                if ext.lower() in IMG_EXT:
                    icons[base] = f"/icons/{fn}?v={int(os.path.getmtime(os.path.join(ICONS_DIR, fn)))}"
        return {"version": self.version, "cases": out, "icons": icons}

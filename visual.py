"""Aprendizado do visual do inventário a partir da própria renderização do jogo.

Enquanto a maleta está aberta no jogo, a tela é capturada e daí se extraem:
  * fundo da tela (moldura, maleta vazia célula por célula, menus)  -> cache/screen_L{n}.png
  * ícone 3D de cada item, retificado (sem perspectiva)             -> cache/items/{id}_r{0|1}.png
  * algarismos da caixinha de quantidade (fonte do jogo)            -> cache/digits/{d}.png
  * painel do Leon segurando a arma equipada, com o medidor limpo   -> cache/leon_{arma}.png
Tudo é gerado localmente a partir do jogo instalado e fica fora do git.

Projeção: cada célula da maleta mede 100 unidades no mundo 3D
(mundo = M_board · (100·u, −100·v, 0)), e a câmera do inventário foi ajustada uma vez
(calib.json, erro 0,19 px). Assim a grade de qualquer tamanho de maleta sai direto da matriz
da maleta lida da memória. Coordenadas de tela normalizadas para REF_W x REF_H (16:9).
"""
import json
import os
import threading
import time
import traceback

import numpy as np
from PIL import Image, ImageDraw, ImageGrab

import capture

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
CALIB_FILE = os.path.join(HERE, "calib.json")
REF_W, REF_H = 1920, 1080
ICON_PX = 96                     # pixels por célula nos ícones retificados

# Geometria fixa da tela de inventário (medida em 2560x1440, convertida para REF)
LAYOUT = {
    "ptas": {"text": [1588, 132, 1742, 198], "patch": [1425, 132, 1575, 198], "right": 1736, "size": 54},
    "name": {"text": [100, 893, 760, 973], "patch": [760, 893, 1160, 973], "left": 120, "size": 34},
    "leon": [1236, 246, 1920, 1080],
    "gauge": {"cx": 1599.6, "cy": 935.55, "lcd_r": 97.3, "r0": 100.5, "r1": 129.0,
              "a0": 1.0, "deg_per_hp": 0.11344, "hp_seg": 240, "segs": 10,
              "digits": [1582, 922, 1675, 1004], "digits_patch": [1534, 926, 1580, 990]},
}


def ensure_dirs():
    for d in ("", "items", "digits"):
        os.makedirs(os.path.join(CACHE, d), exist_ok=True)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ----------------------------------------------------------------- projeção
def _rot(rx, ry, rz):
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def board_homography(cam, M):
    """Homografia (u, v, 1) em células -> pixel REF, dada a matriz 3x4 da maleta."""
    M = np.asarray(M, dtype=float).reshape(3, 4)
    A = np.column_stack([100 * M[:, 0], -100 * M[:, 1], M[:, 3] - np.array(cam["pos"])])
    K = np.array([[cam["f"], 0, -cam["cx"]], [0, -cam["f"], -cam["cy"]], [0, 0, -1.0]])
    H = K @ _rot(*cam["rot"]) @ A
    return H / H[2, 2]


def apply_h(H, u, v):
    x, y, w = H @ np.array([u, v, 1.0])
    return x / w, y / w


def rectify(img, H, x, y, w, h, scale=1.0, px=ICON_PX):
    """Recorta o quadrilátero das células [x,x+w]x[y,y+h] retificando a perspectiva."""
    T = np.array([[1.0 / px, 0, x], [0, 1.0 / px, y], [0, 0, 1]])
    Mx = np.diag([scale, scale, 1.0]) @ H @ T
    Mx = Mx / Mx[2, 2]
    return img.transform((w * px, h * px), Image.PERSPECTIVE, tuple(Mx.ravel()[:8]), Image.BICUBIC)


def number_box(icon):
    """Caixa escura de quantidade no canto inferior direito do ícone retificado: [x0,y0,x1,y1]."""
    a = np.asarray(icon.convert("L"), dtype=float)
    hh, ww = a.shape
    ox, oy = int(ww * 0.25), int(hh * 0.25)
    reg = a[oy:, ox:]
    dark = np.abs(reg - 13) <= 4               # a caixa do jogo é um cinza quase preto uniforme

    def last_run(profile, thr, gap=8):
        idx = np.where(profile > thr)[0]
        if not len(idx):
            return None
        e = s = idx[-1]
        for i in idx[::-1][1:]:
            if s - i <= gap:
                s = i
            else:
                break
        return s, e

    cr = last_run(dark.mean(0), 0.12)
    if not cr:
        return None
    rr = last_run(dark[:, cr[0]:cr[1] + 1].mean(1), 0.3)
    if not rr:
        return None
    c0, c1 = cr
    r0, r1 = rr
    if c1 - c0 < 10 or r1 - r0 < 14 or c1 < reg.shape[1] - 30:
        return None
    return [int(c0 + ox), int(r0 + oy), int(c1 + ox + 1), int(r1 + oy + 1)]


# ------------------------------------------------------------------ aprendiz
class Learner:
    def __init__(self, game):
        self.game = game
        self.lock = threading.Lock()
        self.version = 0
        self.status = "abra o inventário (maleta) no jogo para aprender o visual"
        ensure_dirs()
        self.calib = load_json(CALIB_FILE, {})
        self.meta = load_json(os.path.join(CACHE, "meta.json"), {})
        for k, v in (("items", {}), ("cells", {}), ("digits", []), ("leon", []), ("boards", {})):
            self.meta.setdefault(k, v)
        self.templates = {}

    def _save_meta(self):
        tmp = os.path.join(CACHE, "meta.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.meta, f)
        os.replace(tmp, os.path.join(CACHE, "meta.json"))
        self.version += 1

    def homography(self, level):
        M = self.meta["boards"].get(str(level))
        cam = self.calib.get("camera")
        return board_homography(cam, M) if (M and cam) else None

    def info(self):
        levels = {}
        for k in self.meta["boards"]:
            levels[k] = {"H": self.homography(k).tolist(), "cells": self.meta["cells"].get(k, []),
                         "screen": os.path.exists(os.path.join(CACHE, f"screen_L{k}.png"))}
        return {"version": self.version, "status": self.status, "ref": [REF_W, REF_H], "iconPx": ICON_PX,
                "layout": LAYOUT, "levels": levels, "items": self.meta["items"],
                "digits": self.meta["digits"], "leon": self.meta["leon"],
                "boxH": self.meta.get("box_h"), "boxDigitW": self.meta.get("box_digit_w")}

    # --------------------------------------------------------------- captura
    def run(self):
        while True:
            try:
                time.sleep(0.6 if self.step() else 0.4)
            except Exception:
                traceback.print_exc()
                time.sleep(2)

    def step(self):
        g = self.game
        if not g.connected() or not g.running():
            return False
        snap = g.inventory_screen()
        if not snap:
            return False
        level = str(snap["level"])
        if self.meta["boards"].get(level) != snap["matrix"]:
            self.meta["boards"][level] = snap["matrix"]
            self._save_meta()
        hwnd = capture.find_window(g.p.pid)
        rect = capture.client_rect_on_screen(hwnd) if hwnd else None
        if not rect:
            return False
        l, t, r, b = rect
        if abs((r - l) / (b - t) - 16 / 9) > 0.02:
            self.status = "o jogo não está em 16:9 — aprendizado visual desativado"
            return False
        raw = ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")
        snap2 = g.inventory_screen()           # descarta capturas no meio de uma mudança
        if not snap2 or snap2["sig"] != snap["sig"]:
            return False
        img = raw.resize((REF_W, REF_H), Image.LANCZOS)
        H = self.homography(level)
        if not self._looks_like_case(img, H, snap):
            return False
        with self.lock:
            changed = self._learn(img, raw, raw.width / REF_W, H, snap)
        self.status = "visual sincronizado com o jogo"
        if changed:
            self._save_meta()
        return True

    @staticmethod
    def _looks_like_case(img, H, snap):
        """As linhas da grade têm que estar onde a projeção diz (descarta outras abas e transições)."""
        g = np.asarray(img.convert("L"), dtype=float)
        W, Hc = snap["size"]
        occ = snap["occupied"]
        ok = tot = 0
        for cy in range(Hc):
            for cx in range(W - 1):
                if (cx, cy) in occ or (cx + 1, cy) in occ:
                    continue
                for t in (0.3, 0.7):
                    x, y = apply_h(H, cx + 1, cy + t)
                    xi, yi = int(round(x)), int(round(y))
                    if not (5 <= xi < REF_W - 5 and 0 <= yi < REF_H):
                        continue
                    tot += 1
                    ok += g[yi, xi - 1:xi + 2].max() > (g[yi, xi - 4] + g[yi, xi + 4]) / 2 + 1.5
        return tot >= 6 and ok / tot > 0.55

    def _learn(self, img, raw, s, H, snap):
        level = str(snap["level"])
        W, Hc = snap["size"]
        changed = False
        # o destaque do item sob o cursor "vaza" um pouco para as células vizinhas
        cur = snap["cursor"]
        hot = [cur[0] - 0.35, cur[1] - 0.35, cur[0] + 1.35, cur[1] + 1.35]
        for it in snap["items"]:
            if it["covers_cursor"]:
                hot = [it["x"] - 0.35, it["y"] - 0.35, it["x"] + it["w"] + 0.35, it["y"] + it["h"] + 0.35]

        def near_cursor(x, y, w, h):
            return x < hot[2] and x + w > hot[0] and y < hot[3] and y + h > hot[1]
        # ---- fundo: copia as células vazias (e fora do cursor) para o template
        path = os.path.join(CACHE, f"screen_L{level}.png")
        tpl = self.templates.get(level)
        if tpl is None and os.path.exists(path):
            tpl = Image.open(path).convert("RGB")
        cells = {tuple(c) for c in self.meta["cells"].get(level, [])}
        if tpl is None:
            tpl = img.copy()
            self._patch(tpl, LAYOUT["ptas"]["text"], LAYOUT["ptas"]["patch"])
            self._patch(tpl, LAYOUT["name"]["text"], LAYOUT["name"]["patch"])
            changed = True
        mask = Image.new("L", (REF_W, REF_H), 0)
        dr = ImageDraw.Draw(mask)
        new = 0
        for cy in range(Hc):
            for cx in range(W):
                if (cx, cy) in snap["occupied"] or near_cursor(cx, cy, 1, 1) or (cx, cy) in cells:
                    continue
                # modelos 3D "vazam" alguns pixels para as vizinhas: só aprende célula isolada de itens
                if any((cx + dx, cy + dy) in snap["occupied"] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    continue
                dr.polygon(self._cell_poly(H, cx, cy, W, Hc), fill=255)
                cells.add((cx, cy))
                new += 1
        if new:
            tpl.paste(img, (0, 0), mask)
            changed = True
        known = cells
        unknown = [(cx, cy) for cy in range(Hc) for cx in range(W) if (cx, cy) not in known]
        if unknown and known and (new or level not in self.meta.get("felt_done", {})):
            self._fill_felt(tpl, H, known, unknown, W, Hc)
            self.meta.setdefault("felt_done", {})[level] = True
            changed = True
        if changed:
            tpl.save(path)
            self.templates[level] = tpl
            self.meta["cells"][level] = sorted(cells)
        # ---- ícones (versão sem o "E" de equipada tem prioridade)
        for it in snap["items"]:
            if near_cursor(it["x"], it["y"], it["w"], it["h"]):
                continue
            key = f"{it['id']}_r{it['rot'] & 1}"
            have = self.meta["items"].get(key)
            if have and (not have.get("e") or it["equipped"]):
                continue
            icon = rectify(raw, H, it["x"], it["y"], it["w"], it["h"], scale=s)
            box = number_box(icon) if it["count"] is not None else None
            icon.save(os.path.join(CACHE, "items", key + ".png"))
            self.meta["items"][key] = {"w": it["w"], "h": it["h"], "e": it["equipped"], "box": box,
                                       "t": int(time.time())}
            changed = True
            if box:
                changed |= self._learn_digits(raw, s, H, it, box)
        # ---- painel do Leon segurando a arma equipada
        wid = snap["equipped_id"]
        if wid is not None and wid not in self.meta["leon"]:
            L = LAYOUT["leon"]
            panel = img.crop(tuple(L))
            self._clean_gauge(panel, L[0], L[1])
            panel.save(os.path.join(CACHE, f"leon_{wid}.png"))
            self.meta["leon"].append(wid)
            changed = True
        return changed

    def _learn_digits(self, raw, s, H, it, box):
        """Recorta os algarismos da caixa de quantidade usando o valor conhecido."""
        text = str(it["count"])
        ax, ay = apply_h(H, it["x"] + box[0] / ICON_PX, it["y"] + box[1] / ICON_PX)
        bx, by = apply_h(H, it["x"] + box[2] / ICON_PX, it["y"] + box[3] / ICON_PX)
        crop = raw.crop((int(ax * s) + 1, int(ay * s) + 1, int(bx * s) - 1, int(by * s) - 1))
        if crop.width < 8 or crop.height < 8:
            return False
        if "box_h" not in self.meta:
            self.meta["box_h"] = round(crop.height / s, 2)
            self.meta["box_digit_w"] = round(crop.width / s / len(text), 2)
        a = np.asarray(crop.convert("L"), dtype=float)
        cols = np.where((a > 110).any(0))[0]
        if len(cols) < 2:
            return False
        c0, c1 = cols[0], cols[-1] + 1
        step = (c1 - c0) / len(text)
        changed = False
        for i, ch in enumerate(text):
            if ch in self.meta["digits"]:
                continue
            g = crop.crop((max(0, int(c0 + i * step) - 2), 0, min(crop.width, int(c0 + (i + 1) * step) + 2),
                           crop.height))
            g = g.resize((max(1, round(g.width * 64 / crop.height)), 64), Image.LANCZOS)
            g.save(os.path.join(CACHE, "digits", f"{ch}.png"))
            self.meta["digits"].append(ch)
            changed = True
        return changed

    @staticmethod
    def _cell_poly(H, cx, cy, W, Hc, grow=0.0, border=0.35):
        """Quadrilátero da célula; células da borda incluem a faixa entre a grade e a moldura."""
        u0 = cx - (border if cx == 0 else grow)
        u1 = cx + 1 + (border if cx == W - 1 else grow)
        v0 = cy - (border if cy == 0 else grow)
        v1 = cy + 1 + (border if cy == Hc - 1 else grow)
        return [apply_h(H, u, v) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]

    def _fill_felt(self, tpl, H, known, unknown, W, Hc):
        """Preenche células ainda não vistas vazias com a textura de uma célula vazia conhecida,
        projetada em perspectiva (evita 'fantasmas' de itens no fundo)."""
        Hinv = np.linalg.inv(H)
        for (cx, cy) in unknown:
            # prefere a mesma linha (iluminação) e a mesma situação de borda
            sx, sy = min(known, key=lambda k: (4 * (k[1] - cy) ** 2 + (k[0] - cx) ** 2
                                               + 50 * ((k[0] in (0, W - 1)) != (cx in (0, W - 1)))
                                               + 50 * ((k[1] in (0, Hc - 1)) != (cy in (0, Hc - 1)))))
            # pixel REF -> célula (u,v) -> desloca para a célula-fonte -> pixel REF
            Mx = H @ np.array([[1, 0, sx - cx], [0, 1, sy - cy], [0, 0, 1.0]]) @ Hinv
            Mx /= Mx[2, 2]
            warped = tpl.transform(tpl.size, Image.PERSPECTIVE, tuple(Mx.ravel()[:8]), Image.BICUBIC)
            m = Image.new("L", tpl.size, 0)
            ImageDraw.Draw(m).polygon(self._cell_poly(H, cx, cy, W, Hc, grow=0.04), fill=255)
            tpl.paste(warped, (0, 0), m)

    @staticmethod
    def _patch(img, text_box, patch_box):
        """Cobre uma área de texto esticando uma faixa vazia da mesma barra."""
        src = img.crop(tuple(patch_box)).resize((text_box[2] - text_box[0], text_box[3] - text_box[1]),
                                                Image.BICUBIC)
        img.paste(src, (text_box[0], text_box[1]))

    def _clean_gauge(self, panel, ox, oy):
        """Remove do medidor o que muda (segmentos acesos, marca de vida máxima e dígitos):
        a textura de segmentos apagados é copiada girando múltiplos de um segmento."""
        G = LAYOUT["gauge"]
        a = np.asarray(panel, dtype=float).copy()
        cx, cy = G["cx"] - ox, G["cy"] - oy
        # dígitos do visor: preenche com uma superfície suave ajustada ao fundo do visor
        d = [G["digits"][0] - ox, G["digits"][1] - oy, G["digits"][2] - ox, G["digits"][3] - oy]
        hgt0, wid0 = a.shape[:2]
        ys0, xs0 = np.mgrid[0:hgt0, 0:wid0]
        inside = np.hypot(xs0 - cx, ys0 - cy) < G["lcd_r"] - 6
        rect = (xs0 >= d[0]) & (xs0 < d[2]) & (ys0 >= d[1]) & (ys0 < d[3])
        near = (xs0 >= d[0] - 30) & (xs0 < d[2] + 30) & (ys0 >= d[1] - 8) & (ys0 < d[3] + 30)
        samp = inside & near & ~rect
        if samp.sum() > 50:
            X = np.c_[np.ones(samp.sum()), xs0[samp], ys0[samp], xs0[samp] ** 2, ys0[samp] ** 2, xs0[samp] * ys0[samp]]
            fillm = rect & inside
            Xf = np.c_[np.ones(fillm.sum()), xs0[fillm], ys0[fillm], xs0[fillm] ** 2, ys0[fillm] ** 2,
                       xs0[fillm] * ys0[fillm]]
            for ch in range(3):
                coef, *_ = np.linalg.lstsq(X, a[..., ch][samp], rcond=None)
                a[..., ch][fillm] = Xf @ coef
        seg = G["hp_seg"] * G["deg_per_hp"]
        hgt, wid = a.shape[:2]
        ys, xs = np.mgrid[0:hgt, 0:wid]
        rr = np.hypot(xs - cx, ys - cy)
        band = (rr >= G["r0"] - 2) & (rr <= G["r1"] + 2)
        R, Gc, B = a[..., 0], a[..., 1], a[..., 2]
        lit = band & (((R - B) > 18) | ((Gc - B) > 22) | ((R > 140) & (Gc > 140) & (B > 140)))
        idx = np.argwhere(lit)
        if not len(idx):
            panel.paste(Image.fromarray(np.clip(a, 0, 255).astype("uint8")))
            return
        ang = np.degrees(np.arctan2(-(ys - cy), xs - cx)) % 360
        out = a.copy()
        for k in list(range(1, G["segs"])) + [-k for k in range(1, G["segs"])]:
            if not len(idx):
                break
            yy, xx = idx[:, 0], idx[:, 1]
            th = np.radians(ang[yy, xx] + k * seg)
            r = rr[yy, xx]
            sx = np.clip(np.round(cx + r * np.cos(th)).astype(int), 0, wid - 1)
            sy = np.clip(np.round(cy - r * np.sin(th)).astype(int), 0, hgt - 1)
            good = ~lit[sy, sx]
            out[yy[good], xx[good]] = a[sy[good], sx[good]]
            idx = idx[~good]
        panel.paste(Image.fromarray(np.clip(out, 0, 255).astype("uint8")))

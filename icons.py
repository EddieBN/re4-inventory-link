"""Ícones dos itens: imagens em web/icons/ nomeadas pelo ID, nome interno ou nome exibido do item."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS_DIR = os.path.join(HERE, "web", "icons")
IMG_EXT = (".webp", ".png", ".jpg", ".jpeg")


def icons():
    out = {}
    if os.path.isdir(ICONS_DIR):
        for fn in os.listdir(ICONS_DIR):
            base, ext = os.path.splitext(fn)
            if ext.lower() in IMG_EXT:
                out[base] = f"/icons/{fn}?v={int(os.path.getmtime(os.path.join(ICONS_DIR, fn)))}"
    return out

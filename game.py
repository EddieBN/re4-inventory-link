"""Interface com o Resident Evil 4 (2005, Steam / bio4.exe 1.1.0).

Leitura do inventário é feita direto da memória (cItemMgr / cItem).
Ações que precisam rodar dentro do jogo (equipar arma, consultar tabelas)
são executadas por um pequeno stub x86 injetado na chamada de
cSceSys::scheduler, que roda uma vez por frame na thread principal —
assim a troca de arma acontece sem pausar o jogo e sem condições de corrida.
"""
import ctypes
import struct
import threading
import time

from procmem import Process
from item_names import ITEM_NAMES, ITEM_KEYS

# Tipos (ITEM_INFO.type)
TYPE_NAMES = {0: "outro", 1: "arma", 2: "munição", 3: "granada", 4: "outro", 5: "tesouro",
              6: "recuperação", 7: "item-chave", 8: "bônus", 9: "acessório", 10: "arquivo",
              11: "mapa/maleta", 12: "gema", 13: "tampinha", 14: "importante"}
TYPE_WEAPON, TYPE_WEAPON_MOD = 1, 9
TYPE_AMMO, TYPE_GRENADE, TYPE_RECOVERY, TYPE_KEY, TYPE_IMPORTANT = 2, 3, 6, 7, 14
KNIFE_IDS = {13, 56}
# Usado só até a tabela real (itemInfo do jogo) ser lida — ela exige o jogo rodando frames.
FALLBACK_WEAPONS = {3, 16, 23, 33, 35, 37, 38, 39, 41, 42, 44, 45, 46, 47, 48, 50, 52, 53, 54, 55,
                    56, 62, 64, 65, 71, 81, 82, 83, 107, 108, 109, 148, 153, 171}

# SubScreenWk->board_size_2AA  ->  (largura, altura); extraído de pzlPlayer::init (0x3ea9b0)
CASE_SIZES = {0: (10, 6), 1: (11, 7), 2: (12, 8), 3: (15, 8)}
CASE_NAMES = {0: "S", 1: "M", 2: "L", 3: "XL"}

CITEM_SIZE = 0x0E
PIECE_STRIDE = 0x58

# Layout do bloco injetado
D_MAGIC, D_ORIG, D_CMD, D_ARG1, D_ARG2, D_FN, D_RESULT, D_HEARTBEAT, D_SEQ = (
    0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20)
D_INFOBUF = 0x40           # 0x110 entradas * 8 bytes
CODE_OFF = 0x900
MAGIC = b"RE4M"
CMD_ARM, CMD_ITEMINFO, CMD_CALL2, CMD_MGR_CALL = 1, 2, 3, 4
N_ITEM_IDS = 0x110

ntdll = ctypes.WinDLL("ntdll")


class Asm:
    """Mini-montador x86 com rótulos, só o suficiente para o stub."""

    def __init__(self, base):
        self.base, self.b, self.labels, self.fix = base, bytearray(), {}, []

    def here(self): return self.base + len(self.b)
    def raw(self, *bs): self.b += bytes(bs)
    def u32(self, v): self.b += struct.pack("<I", v & 0xFFFFFFFF)
    def label(self, n): self.labels[n] = self.here()

    def rel32_to(self, target):           # para call/jmp absolutos conhecidos
        self.u32(target - (self.here() + 4))

    def rel32_label(self, name):
        self.fix.append((len(self.b), name)); self.u32(0)

    def done(self):
        for off, name in self.fix:
            struct.pack_into("<i", self.b, off, self.labels[name] - (self.base + off + 4))
        return bytes(self.b)


class Game:
    def __init__(self):
        self.lock = threading.RLock()
        self.p = None
        self.data = None
        self.item_info = {}
        self.charge_cache = {}
        self._hb = (None, 0.0)

    # ------------------------------------------------------------ conexão
    def connected(self):
        return self.p is not None and self.p.alive()

    def attach(self):
        with self.lock:
            p = Process("bio4.exe")
            self.p = p
            a = p.scan("80 B9 C0 4F 00 00 10 0F 84 ? ? ? ? B9")[0]
            self.item_mgr = p.u32(a + 0xE)
            self.fn_arm = p.call_target(p.scan1("8B 0D ? ? ? ? 51 B9 ? ? ? ? E8 ? ? ? ? 5F 5E 5B 8B E5") + 12)
            self.fn_wepchange = p.call_target(p.scan1("6A 01 E8 ? ? ? ? 83 C4 04 E8 ? ? ? ? F6") + 0xA)
            self.sub_screen = p.u32(p.scan1("68 ? ? ? ? E8 ? ? ? ? 68 00 00 00 F0 E8") + 1)
            self.fn_iteminfo = p.call_target(p.scan1("8D 45 ? 50 51 E8 ? ? ? ? 8A 45 ? 83 C4 08") + 5)
            self.fn_chargenum = p.call_target(p.scan1("E8 ? ? ? ? B9 ? ? ? ? 83 C4 ? 66 3B ? 0F 84"))
            self.fn_use = p.scan1("55 8B EC 83 EC 08 56 57 8B 7D 08 8B F1 85 FF 75 0A 5F 32 C0 5E 8B E5 5D "
                                  "C2 04 00 53 0F B7 1F 8D 45 F8 50 53 E8")
            self.fn_erase = p.call_target(p.scan1(
                "E8 ? ? ? ? 8A 45 ? 8B 4D ? 24 ? 66 0F ? ? 8D 04 FD ? ? ? ? 66 0B ? 66 89 53"))
            self.glob_ptr = p.u32(p.scan1("A1 ? ? ? ? B9 FF FF FF 7F 21 48 ? A1") + 1)  # GLOBAL_WK**
            self.cockpit = p.u32(p.scan("FF FE FF FF B9")[0] + 5)
            sched = p.scan1("74 ? B9 ? ? ? ? E8 ? ? ? ? B9 ? ? ? ? E8 ? ? ? ? E8")
            self.sched_thunk = p.call_target(sched + 17)
            # piece_info: tabela {model*, model*, u32 id, u8 w, u8 h, ...} de 0x58 bytes,
            # localizada pela busca linear em pzlPlayer (mov cx, word ptr [piece_info.id])
            ref = p.scan1("66 8B 0D ? ? ? ? 56 0F B7 33 33 C0 66 3B CE")
            self.piece_info = p.u32(ref + 3) - 8
            self.piece_sizes = self._read_piece_table()
            self._install_hook()
            self.item_info = {}
            self.charge_cache = {}
            self._hb = (None, 0.0)
            self._try_load_tables()

    def running(self):
        """True se o jogo está processando frames (ele pausa quando a janela perde o foco)."""
        try:
            hb = self.p.u32(self.data + D_HEARTBEAT)
        except (OSError, TypeError):
            return False
        last, t = self._hb
        if hb != last:
            self._hb = (hb, time.time())
            return last is not None or False
        return time.time() - t < 0.5

    def _try_load_tables(self):
        if self.item_info:
            return True
        try:
            self.item_info = self._read_item_info()
            return True
        except TimeoutError:
            return False

    def item_type(self, iid):
        if self.item_info:
            return self.item_info.get(iid, (0, 0, 0))
        return (TYPE_WEAPON if iid in FALLBACK_WEAPONS else 0, 0, 0)

    def _read_piece_table(self):
        sizes = {}
        for k in range(512):
            e = self.p.read(self.piece_info + k * PIECE_STRIDE, 0x18 + 64)
            iid, w, h = struct.unpack_from("<IBB", e, 8)
            if iid == 0xFFFF:
                break
            mask = e[0x18:0x18 + w * h]
            sizes[iid] = (w, h, [c == 0x31 for c in mask])
        return sizes

    # ---------------------------------------------------------------- hook
    def _suspend(self, on):
        (ntdll.NtSuspendProcess if on else ntdll.NtResumeProcess)(ctypes.c_void_p(self.p.h))

    def _install_hook(self):
        p = self.p
        cur_target = p.call_target(self.sched_thunk)
        if p.u8(self.sched_thunk) != 0xE9:
            raise RuntimeError("Thunk de cSceSys::scheduler inesperado")
        in_module = p.base <= cur_target < p.base + p.size
        if not in_module:
            # hook de uma execução anterior: reaproveita o bloco e recupera o destino original
            old = cur_target - CODE_OFF
            if p.read(old, 4) != MAGIC:
                raise RuntimeError("Scheduler já hookado por outro mod")
            orig = p.u32(old + D_ORIG)
            self._write_jmp(orig)
            time.sleep(0.1)
            self.data = old
        else:
            orig = cur_target
            self.data = p.alloc(0x1000)
        self.orig_sched = orig
        d = self.data
        p.write(d, MAGIC + struct.pack("<I", orig) + b"\0" * (0x40 - 8))
        p.write(d + CODE_OFF, self._build_stub(d), code=True)
        self._write_jmp(d + CODE_OFF)

    def _write_jmp(self, target):
        rel = struct.pack("<i", target - (self.sched_thunk + 5))
        self._suspend(True)
        try:
            self.p.write(self.sched_thunk + 1, rel, code=True)
        finally:
            self._suspend(False)

    def unhook(self):
        with self.lock:
            if self.p and self.p.alive() and self.data:
                try:
                    self._write_jmp(self.orig_sched)
                except OSError:
                    pass
            self.data = None

    def _build_stub(self, d):
        a = Asm(d + CODE_OFF)
        A = lambda off: d + off
        a.raw(0x60, 0x9C)                                   # pushad; pushfd
        a.raw(0xFF, 0x05); a.u32(A(D_HEARTBEAT))            # inc [heartbeat]
        a.raw(0xA1); a.u32(A(D_CMD))                        # mov eax,[cmd]
        a.raw(0x85, 0xC0)                                   # test eax,eax
        a.raw(0x0F, 0x84); a.rel32_label("exit")            # jz exit
        # --- CMD_ARM: ItemMgr->arm(item); se ok -> WeaponChange()
        a.raw(0x83, 0xF8, CMD_ARM)                          # cmp eax,1
        a.raw(0x0F, 0x85); a.rel32_label("c2")
        a.raw(0xFF, 0x35); a.u32(A(D_ARG1))                 # push [arg1]
        a.raw(0xB9); a.u32(self.item_mgr)                   # mov ecx, ItemMgr
        a.raw(0xE8); a.rel32_to(self.fn_arm)                # call cItemMgr::arm (ret 4)
        a.raw(0x0F, 0xB6, 0xC0)                             # movzx eax,al
        a.raw(0xA3); a.u32(A(D_RESULT))                     # mov [result],eax
        a.raw(0x85, 0xC0)
        a.raw(0x0F, 0x84); a.rel32_label("finish")
        a.raw(0xE8); a.rel32_to(self.fn_wepchange)          # call WeaponChange
        a.raw(0xE9); a.rel32_label("finish")
        # --- CMD_ITEMINFO: itemInfo(i, &buf[i]) para todo id
        a.label("c2")
        a.raw(0x83, 0xF8, CMD_ITEMINFO)
        a.raw(0x0F, 0x85); a.rel32_label("c3")
        a.raw(0x31, 0xF6)                                   # xor esi,esi
        a.label("loop")
        a.raw(0x8D, 0x04, 0xF5); a.u32(A(D_INFOBUF))        # lea eax,[esi*8+buf]
        a.raw(0x50, 0x56)                                   # push eax; push esi
        a.raw(0xE8); a.rel32_to(self.fn_iteminfo)
        a.raw(0x83, 0xC4, 0x08)                             # add esp,8
        a.raw(0x46)                                         # inc esi
        a.raw(0x81, 0xFE); a.u32(N_ITEM_IDS)                # cmp esi,N
        a.raw(0x0F, 0x8C); a.rel32_label("loop")            # jl loop
        a.raw(0xE9); a.rel32_label("finish")
        # --- CMD_CALL2: result = fn(arg1, arg2)  (cdecl)
        a.label("c3")
        a.raw(0x83, 0xF8, CMD_CALL2)
        a.raw(0x0F, 0x85); a.rel32_label("c4")
        a.raw(0xFF, 0x35); a.u32(A(D_ARG2))
        a.raw(0xFF, 0x35); a.u32(A(D_ARG1))
        a.raw(0xFF, 0x15); a.u32(A(D_FN))                   # call [fn]
        a.raw(0x83, 0xC4, 0x08)
        a.raw(0xA3); a.u32(A(D_RESULT))
        a.raw(0xE9); a.rel32_label("finish")
        # --- CMD_MGR_CALL: result = (bool) ItemMgr->fn(arg1)   (__thiscall, ret 4)
        a.label("c4")
        a.raw(0x83, 0xF8, CMD_MGR_CALL)
        a.raw(0x0F, 0x85); a.rel32_label("finish")
        a.raw(0xFF, 0x35); a.u32(A(D_ARG1))                 # push [arg1]
        a.raw(0xB9); a.u32(self.item_mgr)                   # mov ecx, ItemMgr
        a.raw(0xFF, 0x15); a.u32(A(D_FN))                   # call [fn]
        a.raw(0x0F, 0xB6, 0xC0)                             # movzx eax,al
        a.raw(0xA3); a.u32(A(D_RESULT))
        a.label("finish")
        a.raw(0xC7, 0x05); a.u32(A(D_CMD)); a.u32(0)        # mov [cmd],0
        a.raw(0xFF, 0x05); a.u32(A(D_SEQ))                  # inc [seq]
        a.label("exit")
        a.raw(0x9D, 0x61)                                   # popfd; popad
        a.raw(0xFF, 0x25); a.u32(A(D_ORIG))                 # jmp [orig]
        return a.done()

    def _exec(self, cmd, arg1=0, arg2=0, fn=0, timeout=2.0):
        """Agenda um comando para a thread principal e espera o resultado."""
        p, d = self.p, self.data
        with self.lock:
            t0 = time.time()
            while p.u32(d + D_CMD) != 0:           # comando anterior pendente
                if time.time() - t0 > timeout:
                    raise TimeoutError("Jogo não está processando frames")
                time.sleep(0.005)
            seq = p.u32(d + D_SEQ)
            p.write(d + D_ARG1, struct.pack("<IIII", arg1, arg2, fn, 0))
            p.w32(d + D_CMD, cmd)
            while p.u32(d + D_SEQ) == seq:
                if time.time() - t0 > timeout:
                    p.w32(d + D_CMD, 0)
                    raise TimeoutError("Jogo não respondeu (pausado/minimizado/carregando?)")
                time.sleep(0.005)
            return p.u32(d + D_RESULT)

    def _read_item_info(self):
        self._exec(CMD_ITEMINFO, timeout=10)
        buf = self.p.read(self.data + D_INFOBUF, N_ITEM_IDS * 8)
        info = {}
        for i in range(N_ITEM_IDS):
            _, typ, defn, maxn = struct.unpack_from("<HBBH", buf, i * 8)
            info[i] = (typ, defn, maxn)
        return info

    def charge_num(self, item_id, cap_level):
        key = (item_id, cap_level)
        if key not in self.charge_cache:
            if not self.running():
                return None
            v = self._exec(CMD_CALL2, item_id, cap_level + 1, self.fn_chargenum) & 0xFFFF
            self.charge_cache[key] = 1 if v == 0x8000 else v
        return self.charge_cache[key]

    # -------------------------------------------------------------- leitura
    def _mgr(self):
        m = self.p.read(self.item_mgr, 0x30)
        (_, _, used_id, _, p_wep, wep_id, to_whom, char, p_item, p_new,
         array_num) = struct.unpack_from("<IiH2sIHBBIIi", m)
        return p_wep, char, p_item, array_num

    def _items_raw(self):
        p_wep, char, p_item, n = self._mgr()
        if not p_item or n <= 0 or n > 1024:
            return p_wep, char, p_item, []
        raw = self.p.read(p_item, (n + 1) * CITEM_SIZE)
        out = []
        for i in range(n + 1):
            iid, num, flag, chr_, w0, w1, x, y, rot, onboard = struct.unpack_from(
                "<HHBBHHbbbB", raw, i * CITEM_SIZE)
            if flag & 1 and chr_ == char:
                out.append(dict(slot=i, addr=p_item + i * CITEM_SIZE, id=iid, num=num,
                                w0=w0, w1=w1, px=x, py=y, rot=rot, onboard=onboard))
        return p_wep, char, p_item, out

    def piece_dims(self, item_id, rot):
        if item_id not in self.piece_sizes:
            return None
        w, h, _ = self.piece_sizes[item_id]
        return (h, w) if rot & 1 else (w, h)

    def case_level(self):
        return self.p.read(self.sub_screen + 0x2AA, 1)[0] & 3

    def state(self):
        with self.lock:
            if not self.item_info and self.running():
                self._try_load_tables()
            p_wep, char, _, items = self._items_raw()
            lvl = self.case_level()
            cw, ch = CASE_SIZES[lvl]
            board, others = [], []
            for it in items:
                iid = it["id"]
                typ, _, maxn = self.item_type(iid)
                e = dict(slot=it["slot"], id=iid, key=ITEM_KEYS[iid] if iid < len(ITEM_KEYS) else str(iid),
                         name=ITEM_NAMES[iid] if iid < len(ITEM_NAMES) else f"Item {iid}",
                         type=typ, typeName=TYPE_NAMES.get(typ, "?"), num=it["num"], max=maxn,
                         equipped=it["addr"] == p_wep, rot=it["rot"])
                if typ == TYPE_WEAPON:
                    w0 = it["w0"]
                    cap = (w0 >> 12) & 0xF
                    e.update(ammo=it["w1"] >> 3, firepower=(w0 & 0xF) + 1, firingSpeed=((w0 >> 4) & 0xF) + 1,
                             reloadSpeed=((w0 >> 8) & 0xF) + 1, capacity=cap + 1)
                    try:
                        e["ammoMax"] = self.charge_num(iid, cap)
                    except TimeoutError:
                        e["ammoMax"] = None
                        self.charge_cache.pop((iid, cap), None)
                dims = self.piece_dims(iid, it["rot"])
                if it["onboard"] == 1 and dims:
                    w, h = dims
                    e.update(w=w, h=h, x=(it["px"] - (w - 1)) // 2, y=(it["py"] - (h - 1)) // 2,
                             baseW=self.piece_sizes[iid][0], baseH=self.piece_sizes[iid][1])
                    board.append(e)
                else:
                    others.append(e)
            vit = self.vitals()
            return dict(connected=True, vitals=vit, caseLevel=lvl, caseName=CASE_NAMES[lvl], caseW=cw, caseH=ch,
                        character=char, items=board, others=others,
                        equippedId=self.p.u16(p_wep) if p_wep else None)

    # ---------------------------------------------------------------- ações
    def _find(self, slot):
        _, _, _, items = self._items_raw()
        for it in items:
            if it["slot"] == slot:
                return it
        raise ValueError("Item não encontrado no inventário")

    def equip(self, slot):
        with self.lock:
            it = self._find(slot)
            if self.item_type(it["id"])[0] != TYPE_WEAPON:
                raise ValueError("Este item não é uma arma")
            p, d = self.p, self.data
            # espera algum comando de outro tipo terminar; um ARM pendente é substituído
            t0 = time.time()
            while p.u32(d + D_CMD) not in (0, CMD_ARM):
                if time.time() - t0 > 2:
                    raise TimeoutError("Jogo ocupado")
                time.sleep(0.005)
            seq = p.u32(d + D_SEQ)
            p.w32(d + D_ARG1, it["addr"])
            p.w32(d + D_CMD, CMD_ARM)
            while p.u32(d + D_SEQ) == seq:
                if time.time() - t0 > 1.5:
                    return "pending"   # jogo pausado: troca acontece assim que voltar a rodar
                time.sleep(0.005)
            if not p.u32(d + D_RESULT):
                raise ValueError("O jogo recusou a troca de arma neste momento")
            return "ok"

    def move(self, slot, x, y, rot):
        with self.lock:
            it = self._find(slot)
            rot = int(rot) & 3
            dims = self.piece_dims(it["id"], rot)
            if not dims or it["onboard"] != 1:
                raise ValueError("Item não fica na maleta")
            w, h = dims
            cw, ch = CASE_SIZES[self.case_level()]
            if x < 0 or y < 0 or x + w > cw or y + h > ch:
                raise ValueError("Fora dos limites da maleta")
            occ = set()
            _, _, _, items = self._items_raw()
            for o in items:
                od = self.piece_dims(o["id"], o["rot"])
                if o["slot"] == slot or o["onboard"] != 1 or not od:
                    continue
                ox, oy = (o["px"] - (od[0] - 1)) // 2, (o["py"] - (od[1] - 1)) // 2
                occ |= {(ox + i, oy + j) for i in range(od[0]) for j in range(od[1])}
            if any((x + i, y + j) in occ for i in range(w) for j in range(h)):
                raise ValueError("Espaço ocupado por outro item")
            px, py = 2 * x + (w - 1), 2 * y + (h - 1)
            self.p.write(it["addr"] + 0xA, struct.pack("<bbb", px, py, rot))

    # ------------------------------------------------- vida / HUD / maleta aberta
    def _glob(self):
        return self.p.u32(self.glob_ptr)

    def vitals(self):
        g = self._glob()
        hp, hp_max, sub_hp, sub_max = struct.unpack("<hhhh", self.p.read(g + 0x4FB4, 8))
        gold = self.p.i32(g + 0x4FA8)
        lm = self.p.read(self.cockpit, 0x6C)          # Cockpit.m_LifeMeter_0
        c0 = struct.unpack_from("<3f", lm, 0x0C)
        c1 = struct.unpack_from("<3f", lm, 0x1C)
        return {"hp": hp, "hpMax": hp_max, "ashleyHp": sub_hp, "ashleyHpMax": sub_max, "gold": gold,
                "lifeColor0": [round(v) for v in c0], "lifeColor1": [round(v) for v in c1],
                "invOpen": bool(self.p.u32(self.sub_screen + 0x2C) & 1)}

    def case_board(self):
        """(nível, matriz 3x4 da maleta) enquanto a maleta está aberta no jogo, senão None."""
        with self.lock:
            if not self.p.u32(self.sub_screen + 0x2C) & 1:          # SS_OPEN_NORMAL
                return None
            pz = self.p.u32(self.sub_screen + 0x2AC)
            board = self.p.u32(pz) if pz else 0
            if not board or self.p.u32(pz + 0x30) != board:
                return None
            b = self.p.read(board, 0x40)
            level = self.case_level()
            if (b[4], b[5]) != CASE_SIZES[level]:
                return None
            return level, [round(v, 4) for v in struct.unpack_from("<12f", b, 0x0C)]

    def _mgr_call(self, fn, arg, timeout=1.5):
        return self._exec(CMD_MGR_CALL, arg, 0, fn, timeout=timeout)

    def use(self, slot):
        """Usa um item de recuperação pelo caminho do próprio jogo (cItemMgr::use)."""
        with self.lock:
            it = self._find(slot)
            if self.item_type(it["id"])[0] != TYPE_RECOVERY:
                raise ValueError("Este item não pode ser usado")
            if not self.running():
                raise ValueError("Volte ao jogo (janela em foco) para usar itens")
            g = self._glob()
            hp, hp_max = struct.unpack("<hh", self.p.read(g + 0x4FB4, 4))
            self.p.w8(self.item_mgr + 0x12, 0)          # m_to_whom = Leon
            if not self._mgr_call(self.fn_use, it["addr"]):
                raise ValueError("Não teve efeito (vida já está cheia?)")
            hp2, hp_max2 = struct.unpack("<hh", self.p.read(g + 0x4FB4, 4))
            return {"hp": [hp, hp2], "hpMax": [hp_max, hp_max2]}

    def discard(self, slot):
        with self.lock:
            it = self._find(slot)
            typ = self.item_type(it["id"])[0]
            if typ in (TYPE_KEY, TYPE_IMPORTANT):
                raise ValueError("Este item não pode ser descartado")
            p_wep = self._mgr()[0]
            if it["addr"] == p_wep:
                raise ValueError("Equipe outra arma antes de descartar esta")
            if not self.running():
                raise ValueError("Volte ao jogo (janela em foco) para descartar")
            self._mgr_call(self.fn_erase, it["addr"])

    def set_count(self, slot, num):
        with self.lock:
            it = self._find(slot)
            typ, _, maxn = self.item_type(it["id"])
            if typ == TYPE_WEAPON:
                raise ValueError("Use o campo de munição para armas")
            num = max(1, min(int(num), maxn or 999))
            self.p.w16(it["addr"] + 2, num)

    def set_ammo(self, slot, ammo):
        with self.lock:
            it = self._find(slot)
            if self.item_type(it["id"])[0] != TYPE_WEAPON:
                raise ValueError("Não é arma")
            cap = (it["w0"] >> 12) & 0xF
            mx = self.charge_num(it["id"], cap)
            if mx is None:
                raise ValueError("Volte ao jogo (janela em foco) para editar munição")
            ammo = max(0, min(int(ammo), mx))
            self.p.w16(it["addr"] + 8, (ammo << 3) | (it["w1"] & 7))

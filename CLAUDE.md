# RE4 Inventory Link — memória do projeto

Mod externo para **Resident Evil 4 (2005) Steam**, `bio4.exe` 1.1.0 (32-bit, em
`C:\Program Files (x86)\Steam\steamapps\common\Resident Evil 4\Bin32\bio4.exe`).
Um servidor Python (só stdlib) lê/escreve a memória do jogo e serve uma página web na intranet
(porta 8044) que espelha a maleta e controla o inventário pelo celular.

## Arquivos
- `procmem.py` — WinAPI via ctypes: abrir processo, ler/escrever, alocar, *pattern scan* na imagem do módulo.
- `game.py` — estruturas do jogo, hook na thread principal, ações (equipar, usar, descartar, mover, editar).
- `server.py` — HTTP + SSE (`/api/events`), API POST (`/api/equip|use|discard|move|count|ammo`), `/api/icons`.
- `icons.py` — lista `web/icons/` (ícones do usuário; fora do git por serem da Capcom).
- `item_names.py` — nomes dos 273 IDs (gerado a partir do re4_tweaks).
- `switchlink.py` — UDP 8045 (pacote `RE4P` 20 bytes → `game.set_pad`, resposta `RE4R` com vibração; watchdog 0,4 s)
  e `/api/switch/state?v=N` (texto, long-poll 8 s; linhas `V`, `S`, `I`, `O`, `E` separadas por TAB).
- `switch/` — homebrew `.nro` (libnx + SDL2/SDL2_ttf/SDL2_image; fonte compartilhada do sistema). Build:
  MSYS2 do devkitPro em `C:\devkitPro\msys64` → `cd /d/RE4/switch && make`. Config no SD: `sdmc:/switch/re4inv/config.txt`.
- `web/index.html` — interface (maleta desenhada, menu Equip/Use/Examine/Discard, arrastar, girar com 2 dedos).

## Decisões do usuário (não reverter sem perguntar)
- Tela mostra **só a maleta, desenhada** (não usar screenshots do jogo como fundo; sem Leon/medidor/PTAS).
- Tamanho da maleta é **automático** (sem seletor S/M/L/XL).
- Ícones dos itens o usuário vai providenciar em `web/icons/` (`{id}.png`, `{NomeInterno}.png` ou nome exibido).
- Ajustes de munição/quantidade ficam em *Examine → Ajustes…*, não na tela principal.
- Interface em português; rótulos do menu em inglês como no jogo (Equip, Use, Examine, Discard).

## Fatos de memória verificados (bio4.exe 1.1.0, padrões do re4_tweaks)
- `cItemMgr` (0x30 bytes): `+0x0C pWep (cItem* equipado)`, `+0x12 m_to_whom (0=Leon)`, `+0x13 char`,
  `+0x14 pItem (array)`, `+0x1C array_num`. Padrão: `80 B9 C0 4F 00 00 10 0F 84 ? ? ? ? B9` (+0xE) — 2 ocorrências, mesmo valor.
- `cItem` (0x0E): `id u16, num u16, flag u8 (bit0=existe), chr u8, w0 u16 (melhorias: 4 bits cada, poder/cadência/recarga/capacidade), w1 u16 (munição<<3), pos_x i8, pos_y i8, rot i8, onboard u8`.
  - Posição: `pos = 2*canto + (tamanho-1)` em cada eixo (centro×2). `rot` ímpar = girado 90° (confirmado: o jogo grava 1).
- Tabela `piece_info`: entradas de 0x58 bytes `{ptr, ptr, u32 id, u8 w, u8 h, …, máscara '1' em +0x18}`, termina em id 0xFFFF.
  Achada pelo padrão `66 8B 0D ? ? ? ? 56 0F B7 33 33 C0 66 3B CE` (+3) − 8.
- Tamanho da maleta: `SubScreenWk+0x2AA` (0..3) → 10×6, 11×7, 12×8, 15×8 (de pzlPlayer::init).
  `SubScreenWk+0x2C` open_flag (bit0 = inventário aberto), `+0x2AC` pzlPlayer.
- `GLOBAL_WK` (ponteiro via `A1 ? ? ? ? B9 FF FF FF 7F 21 48 ? A1` +1): `+0x4FA8 dinheiro`, `+0x4FB4/0x4FB6 vida/vida máx. Leon (int16)`, `+0x4FB8/0x4FBA Ashley`.
- Funções chamadas pelo stub: `cItemMgr::arm` (thiscall, ret 4) + `WeaponChange` (cdecl), `cItemMgr::use` (0x466c90: cura, erva amarela aumenta vida máx., usa m_to_whom),
  `cItemMgr::erase` (descartar), `itemInfo(id, ITEM_INFO*)` (tipo/limite dependem da dificuldade), `WeaponId2ChargeNum(id, nível)`.

## Controle remoto (XInput)
- `bio4.exe` importa por ordinal da `XINPUT1_3.dll`: #2 GetState, #3 SetState, #4 GetCapabilities (IAT achada lendo o PE na memória).
- Stubs no bloco injetado (+0xE40) respondem pelo slot 0 quando `active` (+0xE00) = 1; estado XINPUT_STATE em +0xE04,
  vibração pedida pelo jogo em +0xE18, originais em +0xE20. `unhook` restaura a IAT.
- O jogo chama GetState ~360x/s; teste confirmou o analógico chegando em `JOY.leftStick_X` (Joy em padrão
  `83 E0 10 33 C9 0B C1 0F 84 ? ? ? ? 0F BE 05 ? ? ? ?` → [+0x10] − 9).
- Cheats (easter egg Konami no Switch): `game.cheat()`; modo deus e munição infinita rodam no stub por frame
  (flags em +0x30/+0x34, alvo da munição em +0x38/+0x3C atualizado pelo poller). Dar item: `PutInCase(id, num, tamanho)`
  (cdecl, padrão `0F B7 4E 1C 52 50 51 E8` +7) para itens de maleta, `cItemMgr::get(id, num)` (padrão
  `56 50 B9 ? ? ? ? E8 ? ? ? ? A1` +7) para os demais. Comandos do stub: 5 = cdecl 3 args, 6 = ItemMgr thiscall 2 args.
- Combinações: tabela do jogo `{u16 a, u16 b, u16 resultado}` ×75 (padrão `B8 ? ? ? ? 33 C9 66 3B 30 75 ? 66 3B 50 02 74` +1).
  Armas com acessório (ex.: 36, 38, 50, 108) não têm peça em `piece_info` — combinação recusada por enquanto.
- Controle pelo navegador: Gamepad API exige contexto seguro (Chrome/Firefox) → servidor também em HTTPS 8443 com
  certificado autoassinado gerado por `certgen.py` (RSA 2048 + X.509 em Python puro, em `certs/`, fora do git).
  `/api/pad` é WebSocket (`wsock.py`): pacotes RE4P → `pad_rx.feed()` (mesmo watchdog do UDP). Na página, botão 🎮.
  Steam Deck: Chrome como jogo não Steam + layout de controle "Gamepad" no Steam Input.
- NÃO rodar `game.attach()` em outro processo com o servidor no ar: ao sair ele desfaz o hook do servidor.
- Decisão do usuário: Switch = tela da maleta (mesmo visual da web) + controle; IP digitado no app.

## Hook
- Stub x86 alocado no processo, inserido trocando o destino do *thunk* `jmp cSceSys::scheduler`
  (padrão `74 ? B9 ? ? ? ? E8 ? ? ? ? B9 ? ? ? ? E8 ? ? ? ? E8`, call em +17). Roda 1x por frame na thread principal.
- Bloco de dados com magic `RE4M`: cmd/arg/resultado/heartbeat/seq; código em +0x900. Na reconexão o hook antigo é reaproveitado.
- O processo é suspenso (NtSuspendProcess) por instantes ao reescrever o thunk. Ctrl+C no servidor restaura o thunk.

## Armadilhas conhecidas
- **O RE4 pausa quando a janela perde o foco**: o heartbeat para; trocar arma fica agendado, usar/descartar pedem foco.
- A maleta desliza ao abrir o inventário (a matriz do board muda durante a animação).
- Abrir/alterar o inventário pela web com a maleta aberta no jogo pode dessincronizar a tela do jogo.

## Rodar
`python server.py` (ou `iniciar.bat`) → `http://<ip-do-pc>:8044`. Ctrl+C encerra e remove o hook.

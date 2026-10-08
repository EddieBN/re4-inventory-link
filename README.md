# RE4 Inventory Link

Inventário do **Resident Evil 4 (2005, Steam — bio4.exe 1.1.0)** espelhado no celular, via navegador na rede local,
mostrando a maleta do jogo.

## Como usar
1. Abra o jogo e carregue um save.
2. Dê dois cliques em `iniciar.bat` (ou rode `python server.py`). Não precisa instalar nada além do Python 3.
3. No celular (mesmo Wi-Fi), abra o endereço mostrado no console, ex.: `http://192.168.0.10:8044`.
   - Se o Windows perguntar sobre o firewall, permita o Python em **redes privadas**.
   - No celular, "Adicionar à tela inicial" abre a página em tela cheia.
4. Para sair, feche o console ou aperte Ctrl+C — o hook é removido do jogo.

## Na maleta
- A maleta é desenhada no tamanho atual do inventário do jogo (S 10×6, M 11×7, L 12×8, XL 15×8) e muda sozinha
  quando você troca de maleta.
- **Toque** num item → menu do RE4: *Equip* / *Use* (cura) / *Examine* / *Discard*.
- **Segure e arraste** → move o item; sombra verde = cabe, vermelha = não cabe.
  - Celular: com o item preso no dedo, **gire com dois dedos** para girar o item.
  - PC: botão direito, roda do mouse ou tecla **R** durante o arraste.
- *Examine* mostra munição e melhorias; *Ajustes…* edita munição/quantidade.

## Nintendo Switch (homebrew)
O Switch vira **controle do jogo + tela da maleta** (como um Wii U GamePad): o PC mostra o jogo e o Switch mostra
a mesma maleta da página web; os Joy-Cons/Pro Controller controlam o Leon.

1. Copie `switch/re4inv.nro` para `SD:/switch/re4inv.nro` (ou envie pela rede: no hbmenu aperte **Y** e rode
   `nxlink -a <ip-do-switch> switch/re4inv.nro` no MSYS2 do devkitPro).
2. Abra o **RE4 Inventory** pelo hbmenu (de preferência segurando **R** ao abrir um jogo, para ter memória cheia).
3. O app **encontra o PC sozinho** na rede (broadcast UDP na porta 8045) — inclusive se o roteador trocar o IP do PC.
   Se não achar, toque em **Config.** e digite o IP mostrado no console do `iniciar.bat` (não precisa de porta).
   Fica salvo em `sdmc:/switch/re4inv/config.txt`.
- Toque na tela: menu Equip / Use / Examine / Discard; segure e arraste para mover; gire com dois dedos.
- Botões: **Config.** permite trocar o layout — *posição Xbox* (o botão de baixo é o A do jogo) ou
  *rótulos Nintendo* (A = A). Com um painel aberto, os botões navegam o painel e o Leon fica parado.
- A vibração do jogo é repassada aos Joy-Cons. Se o Switch desconectar, o controle virtual é solto em 0,4 s.
- O PC precisa ficar com a janela do RE4 em foco (o jogo pausa sem foco).

### Compilar o homebrew
Requer o devkitPro (instalado em `C:\devkitPro\msys64`, pacotes `switch-dev switch-sdl2 switch-sdl2_ttf
switch-sdl2_image`). No MSYS2 do devkitPro: `cd /d/RE4/switch && make`.

## Ícones
- `web/icons/`: um arquivo por item, nomeado pelo ID (`35.png`), pelo nome interno (`Shotgun.png`) ou pelo nome
  exibido; vista de cima na orientação padrão. Sem ícone, o item aparece como um bloco com o nome.
- Os ícones são do jogo e ficam fora do git.

## Como funciona
- `procmem.py` — leitura/escrita de memória do processo (ctypes/WinAPI).
- `game.py` — localiza as estruturas por *pattern scan* (`cItemMgr`, `cItem`, `piece_info`, `SubScreenWk`,
  `GLOBAL_WK`) e injeta um stub x86 na chamada de `cSceSys::scheduler`, que roda a cada frame na thread
  principal. O stub chama as funções do próprio jogo: `cItemMgr::arm` + `WeaponChange` (trocar arma),
  `cItemMgr::use` (curar — ervas, spray, ovos, peixe, inclusive aumento de vida máxima), `cItemMgr::erase`
  (descartar), `itemInfo` e `WeaponId2ChargeNum`. O controle remoto entra desviando as importações
  `XInputGetState/SetState/GetCapabilities` do jogo: o RE4 enxerga um controle Xbox no slot 1.
- `icons.py` — lista os ícones de `web/icons/`.
- `switchlink.py` — ponte com o Switch: recebe o controle por UDP (porta 8045) e serve o estado da maleta em texto
  (`/api/switch/state`, long-poll).
- `switch/` — homebrew do Switch (C, libnx + SDL2).
- `server.py` — servidor HTTP (stdlib), API JSON e SSE. `web/index.html` — interface.

## Observações
- O RE4 **pausa quando a janela perde o foco**. Enquanto pausado, a interface mostra "pausado"; trocar de arma fica
  agendado e usar/descartar pede para voltar ao jogo.
- Reorganizar/girar itens altera o inventário na memória; evite fazer isso com a maleta aberta no jogo.
- Os padrões de memória vêm do projeto re4_tweaks; outras versões do executável podem não ser compatíveis.

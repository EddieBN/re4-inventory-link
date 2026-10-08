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

## Controle pelo navegador (celular com controle, Steam Deck, tablet, PC…)
Qualquer aparelho com navegador e um controle (Bluetooth, USB ou acoplado ao celular, como Backbone/Kishi) vira
**tela da maleta + controle do jogo**. O PC entrega o controle ao RE4 como um controle Xbox, com vibração.
1. No aparelho, abra **`https://<ip-do-pc>:8443`** (o endereço aparece no console do `iniciar.bat`).
   Na primeira vez o navegador avisa sobre o certificado: toque em **Avançado → Continuar**. O certificado é gerado
   pelo próprio mod no seu PC (`certs/`); os navegadores só liberam controles em páginas HTTPS.
2. Toque em **🎮 Controle** no topo e aperte qualquer botão do controle.
- Botões no layout Xbox (A embaixo). Tocar no 🎮 de novo desliga.
- **Steam Deck:** no Modo Desktop instale o Google Chrome (Discover) → na Steam, *Adicionar jogo não Steam* → Chrome.
  Em *Propriedades → Opções de inicialização* use
  `--kiosk https://<ip-do-pc>:8443` (o comando já preenchido termina com `@@u @@`; coloque o endereço antes disso).
  No Modo de Jogo, em *Configurações do controle* do Chrome escolha o layout **Gamepad** (o padrão do navegador transforma
  o analógico em mouse e o navegador não vê o controle).

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
- **Easter egg:** no Switch, faça **↑ ↑ ↓ ↓ ← → ← → B A** (D-pad e botões B/A) para liberar o botão **Cheats** ao
  lado de *Keys / Treasures*: vida cheia (Leon/Ashley), + vida máxima, modo deus, munição infinita, recarregar tudo,
  dinheiro, maleta maior e **Dar item** (qualquer item do jogo, por categoria e quantidade). Repita o código para esconder.
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
- `certgen.py` — gera o certificado HTTPS (Python puro, sem OpenSSL). `wsock.py` — WebSocket mínimo para o controle
  pelo navegador (`/api/pad`, mesmo pacote `RE4P` do Switch).
- `switchlink.py` — ponte com o Switch: recebe o controle por UDP (porta 8045) e serve o estado da maleta em texto
  (`/api/switch/state`, long-poll).
- `switch/` — homebrew do Switch (C, libnx + SDL2).
- `server.py` — servidor HTTP (stdlib), API JSON e SSE. `web/index.html` — interface.

## Observações
- O RE4 **pausa quando a janela perde o foco**. Enquanto pausado, a interface mostra "pausado"; trocar de arma fica
  agendado e usar/descartar pede para voltar ao jogo.
- Reorganizar/girar itens altera o inventário na memória; evite fazer isso com a maleta aberta no jogo.
- Os padrões de memória vêm do projeto re4_tweaks; outras versões do executável podem não ser compatíveis.

## Créditos e tecnologias
**Assistente de IA (LLM):** desenvolvido com o **[Claude Code](https://claude.com/claude-code)** (Anthropic, modelo
Claude Opus 5.5), que escreveu o código e fez a engenharia reversa do jogo em execução.

**Projetos de terceiros**
- **[re4_tweaks](https://github.com/nipkownix/re4_tweaks)** (nipkownix e colaboradores) — referência das estruturas do
  jogo (inventário, itens, controle, vida) e dos padrões de memória; nomes dos itens.
- **[devkitPro](https://devkitpro.org/)** — toolchain **devkitA64** e biblioteca **[libnx](https://github.com/switchbrew/libnx)**
  para homebrew do Nintendo Switch.
- **[SDL2](https://www.libsdl.org/)**, **SDL2_ttf** e **SDL2_image** (port do devkitPro) — desenho, fontes e imagens no Switch.
- **[MSYS2](https://www.msys2.org/)** — ambiente onde o devkitPro roda no Windows.
- **[Capstone](https://www.capstone-engine.org/)** — desmontador x86 usado durante o desenvolvimento para analisar o jogo.

**Tecnologias do projeto**
- **Python 3** (só biblioteca padrão: `http.server`, `ctypes`, `socket`, `struct`) — servidor do PC.
- **WinAPI via ctypes** (`ReadProcessMemory`, `WriteProcessMemory`, `VirtualAllocEx`) e *pattern scan* no `bio4.exe`.
- **Assembly x86** montado por um mini-montador em Python — stub injetado que roda a cada frame na thread principal
  do jogo e chama as funções do próprio RE4.
- **Desvio da IAT do XInput** (`XInputGetState/SetState/GetCapabilities`) — controle virtual sem driver, com vibração.
- **HTTP + Server-Sent Events** (página web), **HTTP long-poll** (Switch), **UDP** a 60 Hz (controle) e
  **broadcast UDP** (descoberta automática do PC na rede).
- **HTML, CSS e JavaScript** puros (sem frameworks) — interface do celular.
- **C** com libnx + SDL2 — homebrew do Switch.

Resident Evil 4 é marca da Capcom. Este projeto não é afiliado à Capcom nem à Nintendo e não distribui arquivos do jogo.

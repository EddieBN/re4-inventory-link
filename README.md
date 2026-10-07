# RE4 Inventory Link

Inventário do **Resident Evil 4 (2005, Steam — bio4.exe 1.1.0)** espelhado no celular, via navegador na rede local,
mostrando a maleta do jogo.

## Como usar
1. Abra o jogo e carregue um save.
2. Dê dois cliques em `iniciar.bat` (ou rode `python server.py`). Requer Python 3 com `numpy` e `Pillow`.
3. No celular (mesmo Wi-Fi), abra o endereço mostrado no console, ex.: `http://192.168.0.10:8044`.
   - Se o Windows perguntar sobre o firewall, permita o Python em **redes privadas**.
   - No celular, "Adicionar à tela inicial" abre a página em tela cheia.
4. Para sair, feche o console ou aperte Ctrl+C — o hook é removido do jogo.

## Na maleta
- A tela mostra só a maleta, com a imagem de `web/cases/` do tamanho atual do jogo.
- Barra superior: **Auto** segue o tamanho da maleta no jogo (marcado com •); **S / M / L / XL** mostram uma prévia.
- **Toque** num item → menu do RE4: *Equip* / *Use* (cura) / *Examine* / *Discard*.
- **Segure e arraste** → move o item; sombra verde = cabe, vermelha = não cabe.
  - Celular: com o item preso no dedo, **gire com dois dedos** para girar o item.
  - PC: botão direito, roda do mouse ou tecla **R** durante o arraste.
- *Examine* mostra munição e melhorias; *Ajustes…* edita munição/quantidade.

## Imagens das maletas e ícones
- `web/cases/S.webp`, `M.webp`, `L.webp`, `XL.webp`: screenshot 16:9 do inventário com a maleta vazia
  (qualquer resolução). A grade é calculada sozinha — basta o servidor ter visto aquela maleta aberta no jogo uma
  vez (ele grava a matriz da maleta em `calib.json`).
- `web/icons/`: um arquivo por item, nomeado pelo ID (`35.png`), pelo nome interno (`Shotgun.png`) ou pelo nome
  exibido; vista de cima na orientação padrão. Sem ícone, o item aparece como um bloco com o nome.
- Essas imagens são do jogo e ficam fora do git.

## Como funciona
- `procmem.py` — leitura/escrita de memória do processo (ctypes/WinAPI).
- `game.py` — localiza as estruturas por *pattern scan* (`cItemMgr`, `cItem`, `piece_info`, `SubScreenWk`,
  `GLOBAL_WK`) e injeta um stub x86 na chamada de `cSceSys::scheduler`, que roda a cada frame na thread
  principal. O stub chama as funções do próprio jogo: `cItemMgr::arm` + `WeaponChange` (trocar arma),
  `cItemMgr::use` (curar — ervas, spray, ovos, peixe, inclusive aumento de vida máxima), `cItemMgr::erase`
  (descartar), `itemInfo` e `WeaponId2ChargeNum`.
- `caseview.py` — projeção da grade (câmera do inventário + matriz da maleta, célula = 100 unidades).
- `server.py` — servidor HTTP (stdlib), API JSON e SSE. `web/index.html` — interface.

## Observações
- O RE4 **pausa quando a janela perde o foco**. Enquanto pausado, a interface mostra "pausado"; trocar de arma fica
  agendado e usar/descartar pede para voltar ao jogo.
- Reorganizar/girar itens altera o inventário na memória; evite fazer isso com a maleta aberta no jogo.
- Os padrões de memória vêm do projeto re4_tweaks; outras versões do executável podem não ser compatíveis.

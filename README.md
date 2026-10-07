# RE4 Inventory Link

Inventário do **Resident Evil 4 (2005, Steam — bio4.exe 1.1.0)** espelhado no celular, via navegador na rede local,
com o visual original do jogo.

## Como usar
1. Abra o jogo e carregue um save.
2. Dê dois cliques em `iniciar.bat` (ou rode `python server.py`). Requer Python 3 com `numpy` e `Pillow`.
3. No celular (mesmo Wi-Fi), abra o endereço mostrado no console, ex.: `http://192.168.0.10:8044`.
   - Se o Windows perguntar sobre o firewall, permita o Python em **redes privadas**.
   - No celular, "Adicionar à tela inicial" abre a página em tela cheia. Em paisagem aparece a tela completa do
     inventário; em retrato, a maleta em cima e o Leon com o medidor embaixo.
4. **Abra a maleta no jogo** de vez em quando: o mod copia dali o visual (veja abaixo).
5. Para sair, feche o console ou aperte Ctrl+C — o hook é removido do jogo.

## Na maleta
- **Toque** num item → menu do RE4: *Equip* / *Use* (cura) / *Examine* / *Discard*.
- **Segure e arraste** → move o item; sombra verde = cabe, vermelha = não cabe.
  - Celular: com o item preso no dedo, **gire com dois dedos** para girar o item.
  - PC: botão direito, roda do mouse ou tecla **R** durante o arraste.
- *Examine* mostra o item, munição e melhorias; *Ajustes…* edita munição/quantidade.
- *Keys / Treasures* (canto superior) lista tesouros, arquivos e itens-chave.
- O medidor mostra a vida atual do Leon com as cores do próprio jogo (Fine/Caution/Danger), a marca de vida
  máxima e a munição da arma equipada; o Leon aparece segurando a arma equipada.

## Visual aprendido do jogo
Os ícones do RE4 são modelos 3D renderizados pelo jogo — não existem como imagens. Por isso o mod **aprende** o visual
da própria tela do jogo enquanto a maleta está aberta (`visual.py`):
fundo da tela e da maleta (célula por célula), ícone de cada item (com a perspectiva desfeita), os algarismos das
caixinhas de quantidade e o painel do Leon para cada arma equipada.
Tudo fica em `cache/` (fora do git) e se completa conforme você usa o inventário: item ainda não visto aparece como
um bloco com o nome até a maleta ser aberta com ele dentro (e fora do cursor).

A grade é calculada pela matemática do jogo: cada célula mede 100 unidades no mundo 3D e a câmera do inventário
foi ajustada uma vez (`calib.json`, erro de 0,2 px), então qualquer tamanho de maleta é projetado a partir da matriz
da maleta lida da memória.

## Como funciona
- `procmem.py` — leitura/escrita de memória do processo (ctypes/WinAPI).
- `game.py` — localiza as estruturas por *pattern scan* (`cItemMgr`, `cItem`, `piece_info`, `SubScreenWk`,
  `GLOBAL_WK`, `Cockpit`) e injeta um stub x86 na chamada de `cSceSys::scheduler`, que roda a cada frame na thread
  principal. O stub chama as funções do próprio jogo: `cItemMgr::arm` + `WeaponChange` (trocar arma),
  `cItemMgr::use` (curar — ervas, spray, ovos, peixe, inclusive aumento de vida máxima), `cItemMgr::erase`
  (descartar), `itemInfo` e `WeaponId2ChargeNum`.
- `visual.py` + `capture.py` — aprendizado do visual a partir da tela do jogo.
- `server.py` — servidor HTTP (stdlib), API JSON e SSE. `web/index.html` — interface.

## Observações
- O RE4 **pausa quando a janela perde o foco**. Enquanto pausado, a interface mostra "pausado"; trocar de arma fica
  agendado e usar/descartar pede para voltar ao jogo.
- O aprendizado visual exige o jogo em 16:9 e visível na tela (tela cheia ou janela).
- Reorganizar/girar itens altera o inventário na memória; evite fazer isso com a maleta aberta no jogo.
- Os padrões de memória vêm do projeto re4_tweaks; outras versões do executável podem não ser compatíveis.

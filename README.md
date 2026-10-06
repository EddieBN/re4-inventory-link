# RE4 Inventory Link

Inventário do **Resident Evil 4 (2005, Steam — bio4.exe 1.1.0)** espelhado no celular, via navegador na rede local.

## Como usar
1. Abra o jogo e carregue um save.
2. Dê dois cliques em `iniciar.bat` (ou rode `python server.py`). Não precisa instalar nada além do Python 3.
3. No celular (mesmo Wi-Fi), abra o endereço mostrado no console, ex.: `http://192.168.0.10:8044`.
   - Se o Windows perguntar sobre o firewall, permita o Python em **redes privadas**.
   - No celular, "Adicionar à tela inicial" abre a página em tela cheia.
4. Para sair, feche o console ou aperte Ctrl+C — o hook é removido do jogo.

## O que faz
- Maleta com o tamanho real (S 10×6, M 11×7, L 12×8, XL 15×8), cada item na posição e tamanho exatos, com rotação.
- Armas com munição carregada / capacidade, munição de reserva e níveis de melhoria.
- Troca de arma ao vivo: toque na barra de armas, toque duplo numa arma da maleta ou "Equipar" nos detalhes.
  O jogo **não** é pausado — a troca roda dentro da thread principal do jogo no próximo frame.
- Editar quantidade de itens e munição carregada; "Organizar" para arrastar itens; "Girar".
- Tesouros, arquivos e itens-chave listados abaixo da maleta.
- Atualização em tempo real (Server-Sent Events) em vários dispositivos ao mesmo tempo.

## Como funciona
- `procmem.py` — leitura/escrita de memória do processo (ctypes/WinAPI).
- `game.py` — localiza as estruturas por *pattern scan* (`cItemMgr`, `cItem`, tabela `piece_info`,
  `SubScreenWk`) e injeta um stub x86 de ~180 bytes na chamada de `cSceSys::scheduler`, que roda a cada frame.
  O stub executa `cItemMgr::arm(item)` + `WeaponChange()` (o mesmo caminho do jogo) e consultas a `itemInfo`
  e `WeaponId2ChargeNum` para tipos, limites e capacidade.
- `server.py` — servidor HTTP (stdlib), API JSON e SSE. `web/index.html` — interface.

## Observações
- O RE4 **pausa quando a janela perde o foco**. Enquanto pausado, a interface mostra "pausado" e a troca de arma
  fica agendada até o jogo voltar.
- Reorganizar/girar itens altera o inventário salvo na memória; evite fazer isso com o menu do inventário aberto no jogo.
- Os padrões de memória vêm do projeto re4_tweaks; outras versões do executável podem não ser compatíveis.

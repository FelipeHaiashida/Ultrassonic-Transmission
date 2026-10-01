# Batalha Naval Sonora no navegador

A Batalha Naval da [v4](../../v4/README.md) numa página web. Cada jogada viaja
por som entre dois aparelhos, sem rede: **PC × celular, celular × celular, ou
contra o jogo em Python** (`python v4/batalha_naval_gui.py`). Não precisa
instalar nada, só abrir a página.

## Como jogar

1. Abra a página nos dois aparelhos (ou a página num e o jogo em Python no outro).
2. Escolha **anfitrião** num e **convidado** no outro. O anfitrião atira primeiro.
3. Toque em **Começar** e libere o microfone quando o navegador pedir.
4. Toque numa casa do tabuleiro inimigo para atirar.

Para dar certo:

- volume alto e modo silencioso desligado (no celular, marque **Volume máximo**);
- aparelhos próximos, sala em silêncio;
- tela ligada e página aberta durante a partida (a página pede para a tela não
  apagar, mas nem todo navegador atende).

Se uma mensagem se perder no ar, os botões são os mesmos da interface em
Python: **Repetir tiro** (o outro não ouviu o tiro), **Repetir resposta** (o
outro não ouviu a resposta) e **Parar de ouvir**. Detalhes no
[README da v4](../../v4/README.md#jogar).

O modo **contra o computador** não usa o microfone: as jogadas passam pelo
modem dentro do próprio aparelho. Dá para tocar o áudio de cada jogada e
simular ruído no canal.

## Precisa de HTTPS

O navegador só libera o microfone em página **HTTPS** ou em `localhost`. No
PC, para testar, basta um servidor local:

```bash
python -m http.server 8766 --directory web/batalha-naval
```

e abrir `http://localhost:8766`. No celular, `http://IP-do-PC:8766` **não**
libera o microfone. A página precisa estar publicada num endereço HTTPS, como
o GitHub Pages.

## Arquivos

| Arquivo           | O que faz                                                          |
| ----------------- | ------------------------------------------------------------------ |
| `index.html`      | A página: tela inicial, tabuleiros e painel do modem               |
| `app.js`          | Áudio do navegador (microfone e alto-falante) e o fluxo das jogadas |
| `modem.js`        | O modem da v4 em JavaScript, sem nada de página                    |
| `jogo.js`         | Regras: tabuleiro, frota, CPU e o protocolo de mensagens           |
| `teste_modem.mjs` | Testes do modem sem hardware: `node teste_modem.mjs`               |

## O modem

`modem.js` é uma porta passo a passo de [`v4/transfer_lib.py`](../../v4/transfer_lib.py):
mesmos parâmetros (perfil `padrao`), mesmo detector de sync por proeminência,
mesmo fim por ausência de tom e mesma leitura dos dígitos. É isso que deixa o
navegador conversar com o Python. Se o `transfer_lib.py` da v4 mudar, o
`modem.js` tem que mudar junto.

Como foi conferido:

- **Python → navegador e navegador → Python:** as 40 mensagens do jogo (36
  coordenadas e 4 respostas) geradas de um lado decodificam do outro, com o
  navegador a 48 kHz e a 44,1 kHz.
- **Gravação real da sala** (`gravacao_20261001_145753.wav`, na raiz): o
  receptor em JavaScript chega ao mesmo resultado do Python, com o mesmo
  tamanho de gravação, o mesmo início dos dados e o mesmo hex.
- **No navegador:** com um microfone simulado, a página recebeu tiros,
  respondeu e passou pelos casos de mensagem perdida (repetir tiro, repetir
  resposta, tiro ilegível, resposta perdida seguida de tiro).

A taxa de amostragem é a da placa de som do aparelho (48 kHz ou 44,1 kHz).
Como o protocolo é medido em segundos e em Hz, os dois lados não precisam usar
a mesma taxa.

## O que ainda não foi testado

Uma partida entre aparelhos de verdade. Os pontos que só o hardware vai
mostrar:

- **iPhone:** com o microfone aberto, o Safari pode mandar o som para o
  alto-falante de ligação, que é bem mais baixo;
- **celular em segundo plano ou com a tela apagada:** o navegador pode pausar
  o microfone, e a mensagem daquele momento se perde (a página avisa no
  registro);
- **alto-falante de celular:** é fraco nos graves, e o sync da v4 fica em 1 kHz.

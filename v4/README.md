# Ultrassonic Transmission — v4 (faixa audível para testes)

Mesmo protocolo da v3, com duas mudanças: a faixa de frequência caiu de
quase-ultrassônica (19–21,5 kHz) para **audível de propósito (1–3,5 kHz)**, e o
receptor foi reescrito para funcionar em sala de verdade, com ruído de fundo
(veja [Decodificação](#decodificação)).

## Por que

A v3 usava frequências no limite da audição humana e da resposta de
alto-falantes/microfones de notebook — difícil saber, só de ouvido, se a
transmissão está saindo, se o volume está bom ou se há estalo/corte. Para
depurar era preciso confiar cegamente no código ou usar um analisador de
espectro.

A v4 desloca tudo 18 kHz para baixo, para o centro da faixa onde o ouvido
humano é mais sensível e qualquer hardware de áudio reproduz sem perda. Dá
para acompanhar a transmissão de ouvido: o sync soa como um bipe grave
constante, os dados como uma sequência de bipes mais agudos. Isso facilita
testar em hardware de verdade — o objetivo desta versão.

**Contrapartida**: a faixa deixou de ser discreta. Isso é audível a qualquer
pessoa por perto, não só ao par emissor/receptor. Se o objetivo for
transmissão discreta, use a v3.

O formato do sinal, o loopback, a Batalha Naval e o `sounddevice` opcional
são herdados da v3 sem alteração. O som emitido também não mudou, mas os dois
PCs precisam do `transfer_lib.py` atualizado, porque quem recebe é que usa o
receptor novo.

## Arquivos

| Arquivo             | O que faz                                                |
| ------------------- | -------------------------------------------------------- |
| `transfer_lib.py`   | Protocolo: codificação, decodificação, canal real e loopback |
| `batalha_naval.py`  | O jogo, em modo loopback ou áudio real                    |
| `batalha_naval_gui.py` | O mesmo jogo, com interface gráfica (tkinter)          |
| `loopback_test.py`  | Suíte de testes que roda sem hardware                     |
| `perfil_sweep.py`   | Mede quanto do preâmbulo dá para cortar                   |
| `emissor.py`        | Gera `transmissao.wav` a partir de um texto               |
| `receptor.py`       | Grava pelo microfone e salva a mensagem                   |

## Começando

```bash
pip install numpy scipy
```

Só isso. `sounddevice` é necessário apenas para o modo áudio real:

```bash
pip install sounddevice
```

### Rodar os testes

```bash
python loopback_test.py
```

Não precisa de hardware nenhum. Verifica ida e volta, robustez de alinhamento,
taxa de acerto sob ruído, o teto de gravação e uma partida completa de Batalha
Naval — tudo em poucos segundos.

### Jogar

Com interface gráfica:

```bash
python batalha_naval_gui.py
```

A janela pergunta o modo (contra a CPU, anfitrião ou convidado) e mostra os
dois tabuleiros. Para atirar, clique numa casa do tabuleiro da direita. O
painel do modem mostra cada mensagem trafegando, com o hex e se chegou
inteira. No áudio real, ele mostra também o nível do microfone e o tom do
protocolo que está sendo reconhecido (`SYNC` ou a frequência do dígito). Só usa
`tkinter`, que já vem com o Python.

No áudio real, uma mensagem pode se perder no ar. A interface tem três saídas:

- **Repetir tiro**: enquanto você espera a resposta, toca o mesmo tiro de novo
  e volta a ouvir. Use quando o outro PC não ouviu o tiro. Se depois de 15 s
  nada chegou, a janela sugere repetir. O botão fica bloqueado enquanto o
  microfone está captando um tom do protocolo, para não tocar por cima de uma
  resposta que está chegando.
- **Repetir resposta**: na sua vez, depois de responder a um tiro, toca a
  resposta de novo. Use quando o outro PC não ouviu a sua resposta.
- **Parar de ouvir**: desiste de esperar e conta a jogada como perdida.

Quem espera um tiro e ouve algo ilegível continua ouvindo, para dar tempo de o
tiro repetido chegar. Se a resposta ao seu tiro se perder de vez, o próximo
tiro do adversário é reconhecido como tiro e a partida segue sozinha. Só o
resultado daquele tiro fica desconhecido, e você pode atirar lá de novo.

No terminal:

```bash
python batalha_naval.py
```

Você contra a CPU, com as jogadas passando pelo modem em memória.

Para **ouvir** cada jogada sem precisar de uma segunda máquina, use `--ouvir`:
o áudio toca de verdade no seu alto-falante, mas a decodificação continua
vindo do loopback (não depende do microfone captar o som de volta):

```bash
python batalha_naval.py --ouvir
```

Para o canal de verdade, ponta a ponta, entre duas máquinas:

```bash
python batalha_naval.py --audio anfitriao
```

```bash
python batalha_naval.py --audio convidado
```

Como a faixa agora é audível, dá pra confirmar de ouvido que o sync e os
tons de dados estão saindo antes mesmo de checar se decodificou certo.

## Como funciona

### Codificação

1. O payload é convertido para hexadecimal.
2. Cada dígito hex vira um **tom de 0,25 s** em `2000 Hz + (valor × 100 Hz)`
   — de 2000 Hz (dígito `0`) a 3500 Hz (dígito `f`).
3. Cada tom leva uma **janela Blackman** — sem ela, o corte abrupto espalharia
   estalos audíveis por todo o espectro.
4. Antes dos dados vai um **sync de 1,5 s em 1000 Hz** com fade-in lento de
   0,5 s (a "entrada de veludo"), que evita o clique inicial.

```
[2,5s silêncio] → [1,5s sync @ 1 kHz] → [0,5s silêncio] → [dados 2-3,5 kHz] → [1,0s silêncio]
```

Mesma estrutura da v3, só 18 kHz mais baixa — o gap entre sync e primeiro
tom de dado (1000 Hz) é idêntico em proporção.

### Decodificação

Nenhuma etapa usa um limiar de volume fixo. Um tom é reconhecido por se
**destacar dos vizinhos no espectro** (proeminência local, 6× a mediana em
volta). Isso vale para qualquer volume e qualquer ruído de fundo, inclusive
ruído que não é plano, como o de sala, mais forte nos graves.

1. **Sync:** o tom mais destacado da faixa do protocolo precisa estar em
   1000 Hz por 0,25 s seguidos. Um bloco isolado (palavra, estalo) não dispara.
   Os 0,3 s anteriores entram na gravação, para não perder o começo do sync.
2. **Fim:** 2,5 s seguidos sem nenhum tom do protocolo. O ruído de fundo,
   por mais alto que seja, não segura a gravação.
3. **Alinhamento** (`find_data_start`): acha o sync numa faixa estreita em
   volta de 1000 Hz, depois o primeiro tom na faixa dos dados. Em seguida,
   refina pela grade inteira: soma a energia de todos os tons para cada
   deslocamento candidato.
4. **Leitura:** os 60% centrais de cada tom, procurando o pico só dentro da
   faixa dos dígitos (1,9–3,6 kHz). Graves, vibração e ruído fora da faixa não
   competem com o tom.

### Por que mudou

A versão anterior (herdada da v3) falhou num teste real entre dois PCs, que
está registrado pela ferramenta em [`../diagnostico/`](../diagnostico/README.md):

- o detector de sync tomava ruído de sala por sync em 61% dos blocos;
- o ruído de fundo (pico ~3800) nunca ficava abaixo do limiar fixo de 150,
  então cada recepção só parava no teto de 30 s e engolia a seguinte;
- uma vibração em ~13 Hz era mais forte que os tons, e o decodificador, que
  pegava o maior pico do espectro inteiro, leu 6 de 32 dígitos como lixo.

Resultado na mesma gravação: antes, 0 de 2 transmissões; agora, 2 de 2.

### Loopback

`loopback(sinal)` alimenta o **mesmo** detector de sync e o **mesmo**
decodificador do modo real, só que a partir de um array em vez do microfone:

```python
import transfer_lib as tl

sinal = tl.text_to_signal("B3")
recebido = tl.signal_to_text(tl.loopback(sinal))   # 'B3'
```

## O jogo

Tabuleiro 6×6, três navios (3, 2 e 2 células). O protocolo é minúsculo:

| Mensagem | Bytes | Significado                                    |
| -------- | ----- | ---------------------------------------------- |
| `"B3"`   | 2     | coordenada do tiro                             |
| `"A"`    | 1     | água                                           |
| `"X"`    | 1     | acerto                                         |
| `"D"`    | 1     | navio afundado                                 |
| `"V"`    | 1     | último navio afundado, quem atirou venceu      |

## Números medidos

Tudo abaixo sai do `loopback_test.py` desta versão, não de estimativa.
Como só a frequência mudou (as durações de tom, silêncio e preâmbulo são as
mesmas da v3), a taxa útil e o custo por transmissão são idênticos aos da v3:

| Medida                                    | Valor        |
| ------------------------------------------ | ------------ |
| Taxa útil                                  | 1,25 bytes/s |
| Payload máximo por transmissão (teto 30s)  | 34 bytes     |
| Tiro `"B3"`                                | 7,1 s        |
| Resposta `"X"`                             | 6,3 s        |
| Preâmbulo fixo por transmissão             | 5,5 s        |
| Acerto a 40, 30, 20 e 15 dB de SNR         | 100 %        |
| Acerto a 10 dB                             | 90–100 %     |
| Acerto a 5 dB / 0 dB*                      | 100 % / 95 % |

\* Medido fora da suíte: 20 rodadas de `"Vamos para a praia"` com ruído branco
pelo `loopback`. Antes do conserto do receptor, a 30 dB eram 70% e, a 20 dB,
0%.

## Limitações conhecidas

- **Não é mais discreta.** Ao contrário da v3, a faixa de 1–3,5 kHz é
  claramente audível a qualquer pessoa por perto. Essa versão existe para
  facilitar teste e depuração, não para transmissão dissimulada.
- **O preâmbulo domina o custo**, igual na v3: 5,5 s fixos por transmissão
  contra 0,8 s de dados numa resposta de 1 byte.
- **Sem correção de erro.** Um dígito lido errado corrompe o byte. Rode com
  `--ruido -5` no jogo para ver o efeito.
- **Teto de 30 s** limita o payload a ~35 bytes por transmissão. Suba `max_s`
  se precisar de mais.
- **Modo áudio tem corrida de largada.** O receptor precisa estar escutando
  antes de o emissor tocar. Os 2,5 s de warmup dão alguma folga, mas não é um
  handshake de verdade.

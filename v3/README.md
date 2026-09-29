# Ultrassonic Transmission — v3

Transmissão de dados por **som quase-ultrassônico** (19–21,5 kHz), agora com
**modo loopback** e um **jogo de Batalha Naval** onde cada jogada trafega pelo ar.

## Novidades desta versão

- **Loopback**: rodar o protocolo inteiro em memória, sem microfone nem
  alto-falante. Testar uma mudança passou de "dois computadores e cinco minutos"
  para dois segundos.
- **Batalha Naval**: partida completa em que toda jogada passa pelo modem.
- **Alinhamento dinâmico**: o decodificador procura onde os dados começam em
  vez de assumir um deslocamento fixo de 2,0 s.
- **`sounddevice` virou opcional**: só o modo áudio precisa dele.

## Arquivos

| Arquivo             | O que faz                                                |
| ------------------- | -------------------------------------------------------- |
| `transfer_lib.py`   | Protocolo: codificação, decodificação, canal real e loopback |
| `batalha_naval.py`  | O jogo, em modo loopback ou áudio real                    |
| `loopback_test.py`  | Suíte de testes que roda sem hardware                     |
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

```bash
python batalha_naval.py
```

Você contra a CPU, com as jogadas passando pelo modem em memória. Cada jogada
mostra o hex transmitido e quantos segundos de áudio ela custaria de verdade.

Para ver o canal degradar:

```bash
python batalha_naval.py --ruido 20
```

Entre duas máquinas, com áudio de verdade:

```bash
python batalha_naval.py --audio anfitriao
```

```bash
python batalha_naval.py --audio convidado
```

## Como funciona

### Codificação

1. O payload é convertido para hexadecimal.
2. Cada dígito hex vira um **tom de 0,25 s** em `20000 Hz + (valor × 100 Hz)`.
3. Cada tom leva uma **janela Blackman** — sem ela, o corte abrupto espalharia
   estalos audíveis por todo o espectro.
4. Antes dos dados vai um **sync de 1,5 s em 19000 Hz** com fade-in lento de
   0,5 s (a "entrada de veludo"), que evita o clique inicial.

```
[2,5s silêncio] → [1,5s sync @ 19 kHz] → [0,5s silêncio] → [dados] → [1,0s silêncio]
```

### Decodificação

O receptor roda uma FFT por bloco até reconhecer o sync, grava até 2,5 s de
silêncio contínuo, e então:

1. **Procura onde os dados começam** (`find_data_start`), percorrendo a
   estrutura conhecida: espera o sync ficar audível → espera ele acabar →
   espera o primeiro tom.
2. Lê **os 60% centrais de cada tom**, deixando folga nas duas pontas. Um erro
   de alinhamento de até ~20% da nota ainda decodifica certo.

A v1 usava um deslocamento fixo de 2,0 s após a detecção do sync. Isso só
funcionava se a detecção caísse exatamente no início do tom — e qualquer desvio
de dezenas de milissegundos desalinhava todos os tons seguintes.

### Loopback

`loopback(sinal)` alimenta o **mesmo** detector de sync e o **mesmo**
decodificador do modo real, só que a partir de um array em vez do microfone:

```python
import transfer_lib as tl

sinal = tl.text_to_signal("B3")
recebido = tl.signal_to_text(tl.loopback(sinal))   # 'B3'
```

Com canal degradado, para medir robustez:

```python
tl.loopback(sinal, ruido_db=20)
```

O teto de gravação (`max_s`, 30 s por padrão) é mantido **igual** no loopback e
no microfone de propósito: o loopback mente o menos possível sobre o modo real.

## O jogo

Tabuleiro 6×6, três navios (3, 2 e 2 células). O protocolo é minúsculo:

| Mensagem | Bytes | Significado                                    |
| -------- | ----- | ---------------------------------------------- |
| `"B3"`   | 2     | coordenada do tiro                             |
| `"A"`    | 1     | água                                           |
| `"X"`    | 1     | acerto                                         |
| `"D"`    | 1     | navio afundado                                 |
| `"V"`    | 1     | último navio afundado, quem atirou venceu      |

Se a transmissão chegar corrompida, a jogada é perdida — o jogo não finge que
recebeu. É o que torna o `--ruido` uma demonstração honesta de por que correção
de erro faz falta.

## Números medidos

Tudo abaixo sai do `loopback_test.py`, não de estimativa:

| Medida                                   | Valor        |
| ---------------------------------------- | ------------ |
| Taxa útil                                | 1,25 bytes/s |
| Payload máximo por transmissão (teto 30s) | 35 bytes     |
| Tiro `"B3"`                              | 7,1 s        |
| Resposta `"X"`                           | 6,3 s        |
| Preâmbulo fixo por transmissão           | 5,5 s        |
| Acerto a 40 dB de SNR                    | 100 %        |
| Acerto a 30 dB                           | 95 %         |
| Acerto a 20 dB                           | 60 %         |
| Acerto a 10 dB                           | 0 %          |

## Limitações conhecidas

- **O preâmbulo domina o custo.** São 5,5 s fixos por transmissão contra 0,8 s
  de dados numa resposta de 1 byte — cerca de 78 % de desperdício. Uma partida
  completa dá uns 4,5 minutos de áudio real. Encurtar o warmup e o cooldown é o
  maior ganho disponível, mas depende de teste em hardware de verdade: o
  silêncio inicial existe porque muitas placas cortam os primeiros
  milissegundos de reprodução.
- **Sem correção de erro.** Um dígito lido errado corrompe o byte. Rode com
  `--ruido 15` para ver o efeito.
- **Teto de 30 s** limita o payload a ~35 bytes por transmissão. Suba `max_s`
  se precisar de mais.
- **Faixa no limite do hardware.** 19–21,5 kHz está perto do fim da resposta de
  alto-falantes e microfones de notebook.
- **Modo áudio tem corrida de largada.** O receptor precisa estar escutando
  antes de o emissor tocar. Os 2,5 s de warmup dão alguma folga, mas não é um
  handshake de verdade.

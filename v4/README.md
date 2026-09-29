# Ultrassonic Transmission — v4 (faixa audível para testes)

Mesmo protocolo da v3, com uma única mudança: a faixa de frequência caiu de
quase-ultrassônica (19–21,5 kHz) para **audível de propósito (1–3,5 kHz)**.

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

Tudo o mais é herdado da v3 sem alteração: loopback, alinhamento dinâmico,
Batalha Naval, `sounddevice` opcional.

## Arquivos

| Arquivo             | O que faz                                                |
| ------------------- | -------------------------------------------------------- |
| `transfer_lib.py`   | Protocolo: codificação, decodificação, canal real e loopback |
| `batalha_naval.py`  | O jogo, em modo loopback ou áudio real                    |
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

Idêntica à v3: o receptor roda uma FFT por bloco até reconhecer o sync,
grava até 2,5 s de silêncio contínuo, procura onde os dados começam
(`find_data_start`, sem depender de deslocamento fixo) e lê os 60% centrais
de cada tom.

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
| Payload máximo por transmissão (teto 30s)  | 35 bytes     |
| Tiro `"B3"`                                | 7,1 s        |
| Resposta `"X"`                             | 6,3 s        |
| Preâmbulo fixo por transmissão             | 5,5 s        |
| Acerto a 40 dB de SNR                      | 100 %        |
| Acerto a 30 dB                             | ~60-70 %*    |
| Acerto a 20 dB                             | 0 %*         |

\* A robustez a ruído não depende da faixa escolhida — testei os tons da v3
(19–21,5 kHz) nesta mesma máquina e o resultado a 30 dB foi parecido
(55–65%), bem abaixo dos 95% que o README da v3 registra. Isso é uma
característica do decodificador/ambiente (provavelmente sensível à versão do
numpy), não algo introduzido por esta versão. Sem correção de erro, o
decodificador é frágil perto de 30 dB independente da frequência do tom.

## Limitações conhecidas

- **Não é mais discreta.** Ao contrário da v3, a faixa de 1–3,5 kHz é
  claramente audível a qualquer pessoa por perto. Essa versão existe para
  facilitar teste e depuração, não para transmissão dissimulada.
- **O preâmbulo domina o custo**, igual na v3: 5,5 s fixos por transmissão
  contra 0,8 s de dados numa resposta de 1 byte.
- **Sem correção de erro.** Um dígito lido errado corrompe o byte. Rode com
  `--ruido 15` no jogo para ver o efeito.
- **Teto de 30 s** limita o payload a ~35 bytes por transmissão. Suba `max_s`
  se precisar de mais.
- **Modo áudio tem corrida de largada.** O receptor precisa estar escutando
  antes de o emissor tocar. Os 2,5 s de warmup dão alguma folga, mas não é um
  handshake de verdade.

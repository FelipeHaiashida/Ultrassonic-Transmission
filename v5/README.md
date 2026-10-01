# Ultrassonic Transmission — v5 (resistente a ruído)

Transmissão de dados por som feita para funcionar **em sala com barulho**. O
protocolo mudou por inteiro em relação à v4: outra faixa de frequência, outro
sincronismo, correção de erros e um receptor que não depende de limiar de
volume em lugar nenhum.

> **O que foi e o que não foi testado.** Tudo abaixo foi medido em **simulação**
> (`loopback_test.py`, `comparar_v4.py`), usando inclusive o ruído real da sua
> sala, gravado pelo diagnóstico. A simulação não tem a resposta em frequência
> do seu alto-falante e do seu microfone, controle automático de ganho nem
> cancelamento de ruído do sistema. **A v5 ainda não foi testada em hardware
> real.** O primeiro passo no seu equipamento é `python calibrar.py`
> ([veja abaixo](#antes-de-confiar-no-hardware)).

## O que a v4 mostrou e o que a v5 muda

| Fragilidade da v4 | Na v5 |
| --- | --- |
| Faixa de 1–3,5 kHz, justamente onde está o ruído de sala (voz, ventoinha, TV) | **5–8 kHz.** Na gravação real da sala, o piso de ruído em 1–3,5 kHz ficava em ~15–24 dB; de 4,5 kHz para cima, ~−3 dB (20 a 27 dB abaixo). |
| Sincronismo por um tom de 1 kHz achado por limiar de envelope | **Chirp** (varredura 4,4 → 8,6 kHz) achado por **correlação** com o sinal conhecido. Independe do volume e dá o instante exato. Um chirp descendente no fim marca onde a mensagem acaba. |
| Um tom lido errado corrompia o byte; sem nenhuma correção | **Reed-Solomon** (paridade ≥ 4 B, 50% do payload) + **leitura suave**: tons ambíguos viram *apagamentos*, que custam metade de um erro. |
| Tom repetido (`0000`) podia cair numa ressonância da sala e falhar sempre | Dados **embaralhados** (XOR pseudoaleatório) antes de virar tom. |
| Falha silenciosa: devolvia lixo | **Falha explícita**: se a paridade não repara, o resultado é vazio. |
| Fim da gravação por silêncio (ruído alto segurava a recepção) | Fim pelo **chirp descendente**. Um disparo falso é descartado se aparecer outro chirp de início. |

Mais duas decisões de projeto:

- **Os 16 tons ficam em 5000–8000 Hz, menos de uma oitava** (8000 < 2 × 5000). O
  harmônico de um tom distorcido (alto-falante, microfone saturando) nunca cai
  em outro dígito. Os tons têm 200 Hz de distância (v4: 100 Hz).
- **O sinal tem envoltória constante** (só um tom ou um chirp por vez), então
  tolera volume alto e saturação do microfone sem inventar harmônicos dentro da
  banda. Por isso a v5 emite em 80% do fundo de escala, não 55%.

## Estrutura do sinal

```
[1,0s silêncio] [chirp ↑ 0,6s] [0,15s] [tom][gap][tom][gap]... [0,15s] [chirp ↓ 0,6s] [0,4s]
```

- Cada **byte da palavra-código** vira 2 tons (um por nibble): 200 ms de tom +
  60 ms de silêncio.
- A palavra-código é `payload + paridade`, com `paridade = max(4, ⌈0,5 × payload⌉)`.
- O número de tons entre os dois chirps dá o tamanho da palavra-código, e dele sai
  o tamanho do payload. Não há cabeçalho nem byte de tamanho.
- A distância entre os chirps também mede a **deriva de clock** entre as duas
  placas de som, usada para esticar a grade de leitura.

## Arquivos

| Arquivo | O que faz |
| --- | --- |
| `transfer_lib.py` | Protocolo: codificação, receptor em streaming, decodificação, loopback |
| `reed_solomon.py` | Reed-Solomon sobre GF(256) com apagamentos, só biblioteca padrão |
| `canal_sim.py` | Deformações de canal: ruído de sala gravado, reverberação, apito, rajadas, saturação, deriva |
| `loopback_test.py` | Suíte de testes sem hardware |
| `comparar_v4.py` | Mede v4 e v5 sob o **mesmo** canal simulado |
| `calibrar.py` | Mede a SNR de cada frequência no **seu** alto-falante e microfone |
| `batalha_naval.py` | O jogo, em loopback ou por áudio real |
| `emissor.py` / `receptor.py` | Gera/toca uma mensagem; escuta e decodifica (guarda `ultima_gravacao.wav`) |

## Começando

```bash
pip install numpy scipy            # só isso basta para testes e loopback
pip install sounddevice            # só para áudio real
```

```bash
python loopback_test.py --rapido   # ~30 s
python loopback_test.py --sala ../gravacao_20261001_145753.wav   # completo, com o ruído real da sala
python batalha_naval.py            # jogo contra a CPU, canal em loopback
python batalha_naval.py --ouvir    # idem, tocando cada jogada
python batalha_naval.py --audio anfitriao    # duas máquinas
python batalha_naval.py --audio convidado
```

Mensagem avulsa entre dois PCs:

```bash
python receptor.py                 # PC que recebe, primeiro
python emissor.py "Vamos para a praia" --tocar    # PC que emite
```

`--perfil jogo` (nos **dois** lados) usa temporização curta; o padrão é o
robusto.

## Antes de confiar no hardware

A faixa de 5–8 kHz só vale se o seu alto-falante reproduzir e o seu microfone
captar essa faixa. Alguns de notebook cortam o agudo. Meça:

```bash
python calibrar.py
```

Ele grava 2 s de silêncio (o ruído do ambiente), toca um tom a cada 500 Hz de
500 a 15000 Hz e imprime, para cada frequência, nível, ruído e SNR, comparando
a faixa da v4 com a da v5. Se algum tom da faixa da v5 ficar abaixo de 10 dB,
ele avisa; nesse caso ajuste `fbase` e `step_hz` em `transfer_lib.py` para a
faixa onde a SNR for melhor (mantenha `fbase + 15·step_hz < 2·fbase`).

A análise do `calibrar.py` foi validada contra uma resposta de hardware sintética
conhecida (erro de ~1 dB). A parte de áudio real (`playrec`) só roda no seu
equipamento.

## Números medidos (simulação)

`python comparar_v4.py --sala ../gravacao_20261001_145753.wav` — 20 tentativas por
célula, mesma mensagem (`"Ola mundo"`, 9 bytes), as duas versões **no mesmo
pico de sinal** (55% do fundo de escala, o da v4). "Sala gravada +N dB" é o ruído
da sua gravação real multiplicado por N dB, com o sinal a 55% do fundo de escala
(na gravação real o sinal chegou a saturar, então +0 dB é um cenário mais duro
que o seu teste).

| Cenário | v4 | v5 |
| --- | ---: | ---: |
| sala em silêncio | 100% | 100% |
| ruído branco 0 dB (igual ao sinal) | 70% | 100% |
| ruído branco −6 dB / −12 dB / −20 dB | 0% | 100% |
| apito de 3 kHz a −10 dB | 0% | 100% |
| apito de 6,5 kHz a +6 dB (mais forte que o sinal) | 0% | 100% |
| microfone saturando (+20 dB de ganho) | 0% | 100% |
| sala gravada +0 dB | 95% | 100% |
| sala gravada +10 dB | 80% | 100% |
| sala gravada +20 dB | 45% | 100% |
| sala gravada +30 dB / +40 dB | 0% | 100% |
| sala gravada +50 dB | 0% | 30% |
| sala difícil: +20 dB, reverb, rajadas, saturação | 15% | 100% |
| ruído rosa de RMS 20000 | 15% | 100% |
| reverberação 2,0 s | 0% | 90% |

Perfil `jogo`, mensagem `"B3"`: v5 100% em todos os cenários de ruído até a sala
gravada +30 dB, 85% a +40 dB, contra 0% da v4 a partir de +30 dB.

**Onde a v5 quebra**, medido: ruído branco de banda cheia a ~22–24 dB acima do
sinal; ruído de sala a ~+50 dB sobre a gravação (aí o próprio conversor satura
nos graves); reverberação acima de ~2 s. Nesses casos ela devolve vazio, nunca
texto errado.

### Custo (o preço da robustez)

| | v4 padrão | v5 padrão | v5 `jogo` |
| --- | ---: | ---: | ---: |
| Preâmbulo fixo | 5,5 s | 2,9 s | 1,8 s |
| Tiro `"B3"` | 7,1 s | 6,0 s | 3,7 s |
| Resposta `"X"` | 6,3 s | 5,5 s | 3,4 s |
| 9 bytes | 12,7 s | 10,2 s | — |
| Taxa em mensagens longas | 1,25 B/s | 1,28 B/s | — |

Mensagens curtas ficam mais rápidas e as longas ficam do mesmo tamanho, mesmo
com 50% de paridade, porque os tons são mais curtos (260 ms por nibble contra
400 ms). A v4 `jogo` leva 3,1 s num `"B3"`; a v5 `jogo`, 0,6 s a mais.

## Limitações conhecidas

- **Não testada em hardware real** (veja o aviso no topo).
- **Máximo de 170 bytes por transmissão** (limite do Reed-Solomon sobre GF(256);
  ~2,3 min de áudio). Para mais, mande várias transmissões.
- **Ainda é audível**, e agora mais agudo (5–8 kHz). Não é transmissão discreta.
- **Taxa de amostragem fixa em 48 kHz.** Se a placa de som não aceitar, ajuste `Fs`
  (e confira que `fbase + 15·step_hz` fica abaixo de `Fs/2`).
- **Emissor e receptor precisam do mesmo perfil e dos mesmos parâmetros.**
- **O receptor espera o chirp de início.** Se o receptor ligar no meio de uma
  transmissão, ela se perde (não há handshake).
- **Pode errar sem avisar, com probabilidade muito baixa.** Dano muito além da
  correção é recusado quase sempre; no teste isolado, 1 em 300 palavras com 3
  erros (acima da capacidade de 2) decodificou para um valor errado.
- **O diagnóstico detalhado (`../diagnostico/`) cobre até a v4.** Para a v5, use
  `calibrar.py` e `receptor.py`.
- `perfil_sweep.py` da v4 não foi portado: a varredura de preâmbulo foi substituída
  pelo `comparar_v4.py` e pelos dois perfis.

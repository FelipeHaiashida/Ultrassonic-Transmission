# Ultrassonic Transmission

Transmissão de arquivos por **som quase-ultrassônico**. O emissor transforma um
arquivo de texto em tons de áudio entre 19 e 21,5 kHz; o receptor escuta pelo
microfone e reconstrói o arquivo. Sem cabo, sem rede, sem Bluetooth — só o
alto-falante de um lado e o microfone do outro.

## Arquivos

| Arquivo           | O que faz                                                    |
| ----------------- | ------------------------------------------------------------ |
| `transfer_lib.py` | Biblioteca: codificação, geração do WAV, gravação e decodificação |
| `emissor.py`      | Cria `mensagem.txt` e gera `transmissao.wav`                 |
| `receptor.py`     | Grava pelo microfone e salva `mensagem_recebida.txt`         |

## Como funciona

### Emissão

1. O arquivo é lido em binário e convertido para **hexadecimal** (`fileToHex`).
2. Cada dígito hex (`0`–`f`) vira um **tom de 0,25 s**, numa frequência definida por
   `20000 Hz + (valor × 100 Hz)` — ou seja, `0` = 20000 Hz e `f` = 21500 Hz.
3. Cada tom recebe uma **janela Blackman**, que suaviza o começo e o fim da nota.
   Sem isso, o corte abrupto geraria estalos audíveis espalhados por todo o espectro.
4. Entre um tom e o outro há um **silêncio de 0,15 s**, para separar as notas.
5. Antes dos dados vai um **tom de sincronismo de 1,5 s em 19000 Hz**, com um
   fade-in lento de 0,5 s (a "entrada de veludo") — a rampa suave impede o clique
   inicial que denunciaria a transmissão.

O WAV final fica montado assim:

```
[2,5 s silêncio] → [1,5 s sync @ 19 kHz] → [0,5 s silêncio] → [dados] → [1,0 s silêncio]
     warmup                                                                  cooldown
```

O silêncio inicial dá tempo para o hardware de áudio "acordar" — muitas placas
cortam os primeiros milissegundos de reprodução.

### Recepção

1. O receptor lê o microfone em blocos de 1024 amostras e roda uma **FFT** em cada bloco.
2. Quando a frequência dominante cai em **19000 ± 300 Hz**, ele reconhece o sync e
   começa a gravar.
3. Para de gravar após **2,5 s de silêncio contínuo** ou 30 s no total.
4. Na decodificação, pula um **offset fixo de 2,0 s** (1,5 s de sync + 0,5 s de
   silêncio) e a partir daí fatia o sinal em janelas de 0,25 s.
5. Para cada fatia, mede a frequência dominante e faz o caminho inverso:
   `valor = (freq − 20000) / 100`.
6. Junta os dígitos hex e converte de volta para texto.

## Como usar

```bash
python emissor.py
```

Isso gera `transmissao.wav`. Em outra máquina (ou outro terminal), rode o receptor
**antes** de tocar o áudio:

```bash
python receptor.py
```

Com o receptor já escutando, toque o `transmissao.wav` em qualquer player. O
resultado aparece em `mensagem_recebida.txt`.

## Dependências

```bash
pip install numpy scipy pyaudio
```

No Windows, se o `pyaudio` falhar na instalação, use `pip install pipwin` seguido de
`pipwin install pyaudio`.

## Parâmetros

Ficam todos no topo de `transfer_lib.py`:

| Parâmetro  | Valor    | Significado                              |
| ---------- | -------- | ---------------------------------------- |
| `dur`      | 0,25 s   | Duração de cada tom de dado              |
| `gap`      | 0,15 s   | Silêncio entre tons                      |
| `syncDur`  | 1,5 s    | Duração do tom de sincronismo            |
| `Fs`       | 48000 Hz | Taxa de amostragem                       |
| `fsync`    | 19000 Hz | Frequência do sincronismo                |
| `fbase`    | 20000 Hz | Frequência do dígito hex `0`             |
| `step_hz`  | 100 Hz   | Distância entre dois dígitos consecutivos |

## Limitações conhecidas

- **Velocidade:** cada dígito hex leva 0,4 s → cerca de **1,25 bytes/s**. Uma
  mensagem de 19 caracteres leva uns 20 segundos no total.
- **Duração máxima:** o `record()` para em 30 s, o que limita o payload a
  aproximadamente **30 bytes** por transmissão.
- **Alinhamento por offset fixo:** o decodificador assume que a gravação começou
  exatamente no início do tom de sync. Se a detecção disparar alguns
  milissegundos adiante, todos os tons subsequentes saem desalinhados.
- **Sem correção de erro:** um único dígito lido errado corrompe o byte
  correspondente. Não há checksum nem redundância.
- **Dependência de hardware:** 19–21,5 kHz está no limite da resposta de
  alto-falantes e microfones de notebook, que costumam atenuar bastante acima de
  18 kHz.

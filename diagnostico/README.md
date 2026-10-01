# Diagnóstico de sincronia entre 2 PCs

Ferramenta separada do jogo, só para descobrir **por que** a transmissão entre
dois computadores funciona ou falha. O jogo só diz "resposta ininteligível";
aqui cada etapa do caminho é medida isoladamente.

## Como usar

Nos dois PCs (o protocolo testado é o da `v4` por padrão):

```bash
pip install -r requirements.txt
cd diagnostico
python diagnostico_sync.py info
```

No **PC que recebe** (comece primeiro):

```bash
python diagnostico_sync.py receber --duracao 60
```

No **PC que emite**:

```bash
python diagnostico_sync.py emitir --repeticoes 3
```

Quando o emissor terminar, aperte `Ctrl+C` no receptor (ou espere a duração
acabar). Ele salva `gravacao_<data>.wav` e `gravacao_<data>_relatorio.txt`.
**Mande os dois arquivos** junto com a issue: o `.wav` pode ser reanalisado
depois em qualquer máquina:

```bash
python diagnostico_sync.py analisar gravacao_20260929_153000.wav
```

Sem hardware nenhum, dá para ver como cada fator afeta o receptor:

```bash
python diagnostico_sync.py simular --ruido-rms 0     # sala silenciosa: tudo passa
python diagnostico_sync.py simular --ruido-rms 60    # ruído de sala comum
python diagnostico_sync.py simular --ruido-rms 30 --reverb 0.3 --nivel 1500
```

Opções gerais (antes do comando): `--versao v3` testa o protocolo da v3,
`--perfil jogo` usa a temporização curta do jogo, `--hex` troca a mensagem de
teste (tem que ser igual nos dois PCs). `--entrada N` / `--saida N` escolhem o
dispositivo pelo índice mostrado em `info`.

## O que o relatório mede

A mensagem de teste é o hex `0123456789abcdef`, que passa pelos 16 tons.

| Etapa | Pergunta | Se falhar |
| ----- | -------- | --------- |
| 1. Nível | O microfone ouviu? Houve clipping ou overflow? | volume, dispositivo errado, outro programa usando o áudio |
| 2. Canal acústico | Com alinhamento **ideal** (correlação com o sinal conhecido), cada tom chega na frequência certa? SNR de cada um | hardware/ambiente: alto-falante, distância, eco, filtros do Windows |
| 3. Detector de sync | O detector do `transfer_lib` dispara com ruído ambiente? | software |
| 4. Detecção de fim | O ruído de fundo fica abaixo do `SILENCE_FLOOR`? | software/limiar |
| 5. Receptor real | Rodando o receptor do `transfer_lib` sobre a gravação: quando começou, por que parou, quanto errou o alinhamento, o que decodificou | — |

A regra de leitura: **se a etapa 2 passa e a 5 falha, o som chegou certo e o
problema é o software do receptor**. Se a 2 já falha, é o canal físico.

## O que o diagnóstico encontrou (e já foi corrigido na v4)

No primeiro teste real entre dois PCs, o som chegou certo (o receptor ideal
decodificou 2 de 2), mas o receptor antigo do `transfer_lib` acertou 0 de 2:

1. **O detector de sync disparava com ruído**: 61% dos blocos de puro ruído
   de sala eram tomados por sync.
2. **`SILENCE_FLOOR = 150` fixo**: o ruído de fundo (pico ~3800) nunca ficava
   abaixo disso, e cada recepção só parava no teto de 30 s, engolindo a
   transmissão seguinte.
3. **O decodificador pegava o maior pico do espectro inteiro**: uma vibração em
   ~13 Hz, mais forte que os tons, fez 6 de 32 dígitos virarem lixo.

O receptor da v4 foi reescrito para usar proeminência local em vez de limiares
fixos (detalhes em [`../v4/README.md`](../v4/README.md#decodificação)). Na
mesma gravação, ele acerta 2 de 2. A **v3 ainda tem o receptor antigo** e, com
`--versao v3`, esta ferramenta continua mostrando esses três problemas.

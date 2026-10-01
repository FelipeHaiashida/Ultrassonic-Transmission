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

## O que as simulações já mostram

Rodando `simular` com ruído rosa realista (pico ~150–250 em int16, comum em
microfone de notebook), o receptor ideal decodifica 100% das transmissões,
mas o receptor do `transfer_lib` (v4) tem três problemas:

1. **O detector de sync dispara com ruído.** Ele zera só até ~940 Hz e aceita o
   maior pico do bloco, sem exigir que se destaque do ruído. Ruído de sala é
   mais forte nos graves, então o pico cai logo acima do corte — dentro da
   janela do sync (700–1300 Hz). Cerca de metade dos blocos de puro ruído
   dispara. No jogo, `receber_texto()` devolve a primeira recepção, então um
   disparo falso vira "jogada perdida".
2. **`SILENCE_FLOOR = 150` é baixo demais** para muitos microfones: se o ruído
   de fundo passa disso, o receptor nunca vê silêncio e só para no teto de
   30 s, engolindo a transmissão seguinte.
3. **`find_data_start` pode devolver o início do sync em vez dos dados** quando
   a gravação começa antes do sync (por causa do item 1). No perfil `padrao`
   isso é mascarado por coincidência: sync + midgap = 2,0 s = exatamente 5
   passos de tom, então a grade de leitura ainda cai nos tons.

A v3 (19 kHz) não sofre do item 1 com ruído de sala, mas sofre do item 2.

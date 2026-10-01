"""
transfer_lib - transmissao de dados por audio, em faixa audivel de teste.

Cada nibble (digito hex) vira um tom entre 2000 e 3500 Hz. Antes dos dados
vai um tom de sincronismo em 1000 Hz com fade-in lento.

Essa e a mesma estrutura da v3 (fsync -> 1000 Hz de vao -> fbase, digitos a
cada 100 Hz), so que 18 kHz mais baixa: a v3 usava 19000-21500 Hz, quase
ultrassonico, dificil de ouvir e perto do limite de resposta de alto-falantes
e microfones de notebook. Aqui a faixa (1-3,5 kHz) cai bem no centro da
sensibilidade do ouvido humano e qualquer hardware de audio reproduz sem
perda - da para acompanhar a transmissao de ouvido durante o teste, checando
se o sync soa diferente dos dados, se o volume esta ok, se ha estalos. O
preco e que a faixa deixa de ser discreta: agora e um bipe bem audivel, o que
e o objetivo desta versao (testes), nao de uso disfarcado.

Duas formas de usar o canal:

    real      tocar(sinal) / gravar()     precisa de sounddevice
    loopback  loopback(sinal)             so numpy, roda em qualquer maquina

O modo loopback passa pelo MESMO detector de sync e pelo MESMO decodificador
que o modo real - so nao passa pelo ar. Serve para testar o protocolo sem
montar a cerimonia de dois computadores.
"""

import binascii
from collections import deque

import numpy as np
from scipy.io import wavfile
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt

# --- Parametros do canal ---
dur      = 0.25     # duracao de cada tom de dado
gap      = 0.15     # silencio entre tons
syncDur  = 1.5      # duracao do tom de sincronismo
Fs       = 48000    # taxa de amostragem
fsync    = 1000     # frequencia do sincronismo (faixa audivel, facil de ouvir)
fbase    = 2000     # frequencia do digito hex 0
step_hz  = 100      # distancia entre digitos consecutivos (digitos vao ate 3500 Hz)

WARMUP   = 2.5      # silencio inicial (o hardware de audio precisa acordar)
MIDGAP   = 0.5      # silencio entre o sync e os dados
COOLDOWN = 1.0      # silencio no fim
FADE_IN  = 0.5      # teto da rampa de entrada do sync
FIM_SILENCIO = 2.5  # silencio continuo que encerra a recepcao

AMPLITUDE     = 30
CHUNK         = 1024    # tamanho do bloco de leitura do microfone
MAX_RECORD_S  = 30      # teto de gravacao, em segundos

# Deteccao de tom. Nada aqui e um limiar de volume fixo: um limiar fixo (a
# versao anterior usava 150 em int16) quebra em qualquer sala cujo ruido de
# fundo passe dele - medido num teste real entre dois PCs, o ruido estava em
# ~3800. Em vez disso, um tom e reconhecido por se DESTACAR do resto da faixa
# do protocolo, o que vale para qualquer volume e qualquer ruido de fundo.
PROEMINENCIA = 6.0      # pico / mediana da faixa para contar como tom (~16 dB)
TOM_MINIMO   = 30       # amplitude minima (int16) - so separa tom de silencio digital
SYNC_MIN_S   = 0.25     # o sync precisa se sustentar por isso (ate 40% de syncDur)
PRE_ROLL_S   = 0.3      # audio anterior ao sync que entra na gravacao
JANELA_RUIDO_HZ = 800   # vizinhanca usada para medir o ruido em volta de um pico


# Perfis de temporizacao. O preambulo (warmup + sync + midgap + cooldown) e
# fixo por transmissao, entao ele domina o custo de mensagens curtas: numa
# resposta de 1 byte sao 5.5s de preambulo para 0.8s de dados.
PERFIS = {
    # Conservador. Bom para arquivo, hardware desconhecido, primeira tentativa.
    'padrao': dict(syncDur=1.5, WARMUP=2.5, MIDGAP=0.5, COOLDOWN=1.0,
                   FADE_IN=0.5, FIM_SILENCIO=2.5),
    # Para trocas curtas e frequentes, com o receptor ja escutando.
    # Os valores saem de perfil_sweep.py, nao de chute.
    'jogo':   dict(syncDur=0.6, WARMUP=0.4, MIDGAP=0.25, COOLDOWN=0.3,
                   FADE_IN=0.2, FIM_SILENCIO=0.8),
}


def usar_perfil(nome):
    """Troca a temporizacao. Emissor e receptor precisam usar o MESMO perfil:
    o receptor le syncDur e FIM_SILENCIO para saber quando comecar e parar."""
    if nome not in PERFIS:
        raise ValueError(f"perfil desconhecido: {nome!r}. Use um de {list(PERFIS)}")
    globals().update(PERFIS[nome])
    return nome


def preambulo_s():
    """Segundos gastos por transmissao independente do tamanho do payload."""
    return WARMUP + syncDur + MIDGAP + COOLDOWN


# ---------------------------------------------------------------- codificacao

def apply_blackman_fade(signal):
    """Suaviza o inicio e o fim de uma nota. Sem isso o corte abrupto
    espalha estalos audiveis por todo o espectro."""
    return signal * np.blackman(len(signal))


def apply_super_slow_fade(signal, Fs=Fs):
    """Rampa de entrada do tom de sync (a 'entrada de veludo'). A subida lenta
    evita o clique que denunciaria a transmissao.

    A rampa e proporcional ao sync, limitada por FADE_IN. Se fosse fixa em
    0.5s, um sync curto viraria rampa do inicio ao fim e nunca chegaria na
    amplitude cheia - justamente o que o detector precisa enxergar.
    """
    fade_len = min(int(FADE_IN * Fs), int(len(signal) * 0.33))
    t = np.linspace(-np.pi / 2, 0, fade_len)
    signal[:fade_len] *= np.sin(t) + 1

    fade_out_len = int(len(signal) * 0.1)
    signal[-fade_out_len:] *= np.linspace(1, 0, fade_out_len)
    return signal


def _make_sync():
    t = np.linspace(0, syncDur, int(syncDur * Fs), endpoint=False)
    return apply_super_slow_fade(AMPLITUDE * np.sin(2 * np.pi * fsync * t))


def hex_to_signal(hexstr):
    """String hexadecimal -> vetor de audio (float)."""
    t_tone = np.linspace(0, dur, int(dur * Fs), endpoint=False)
    silence_gap = np.zeros(int(gap * Fs))

    partes = []
    for char in hexstr:
        f = fbase + step_hz * int(char, 16)
        tom = AMPLITUDE * np.sin(2 * np.pi * f * t_tone)
        partes.append(apply_blackman_fade(tom))
        partes.append(silence_gap)
    dados = np.concatenate(partes) if partes else np.zeros(0)

    return np.concatenate((
        np.zeros(int(WARMUP * Fs)),
        _make_sync(),
        np.zeros(int(MIDGAP * Fs)),
        dados,
        np.zeros(int(COOLDOWN * Fs)),
    ))


def bytes_to_signal(data):
    return hex_to_signal(binascii.hexlify(data).decode('ascii'))


def text_to_signal(texto):
    return bytes_to_signal(texto.encode('utf-8'))


def normalize_audio(audio_data):
    m = np.max(np.abs(audio_data))
    if m == 0:
        return audio_data.astype(np.int16)
    # Volume em 50% do fundo de escala, para evitar distorcao harmonica
    return ((audio_data / m) * 18000).astype(np.int16)


def write_wav(signal, outputFile='output.wav'):
    wavfile.write(outputFile, Fs, normalize_audio(signal))
    return outputFile


def writeFileToWav(filename, outputFile='output.wav'):
    with open(filename, 'rb') as f:
        dados = f.read()
    write_wav(bytes_to_signal(dados), outputFile)
    return True


# -------------------------------------------------------------- alinhamento

def _envelope(signal, win=256):
    """Amplitude de pico em janelas curtas. Usado para achar onde o
    sinal comeca e termina, sem depender de offsets fixos."""
    n = len(signal) // win
    if n == 0:
        return np.zeros(0), win
    blocos = np.abs(signal[:n * win].reshape(n, win))
    return blocos.max(axis=1), win


def _filtrar(signal, lo, hi):
    """Passa-faixa de fase zero (nao desloca o sinal no tempo)."""
    sos = butter(6, [lo, hi], btype='bandpass', fs=Fs, output='sos')
    return sosfiltfilt(sos, signal)


def find_data_start(signal):
    """Devolve o indice do primeiro tom de dado.

    Percorre a estrutura conhecida do sinal:
        [silencio] -> [sync tocando] -> [silencio] -> [primeiro tom de dado]
                                                       ^ retorna aqui

    Cada etapa olha so a sua faixa de frequencia: o sync numa faixa estreita
    em volta de fsync, os dados na faixa dos digitos. A versao anterior
    olhava a amplitude bruta, e qualquer ruido grave da sala (ventoinha,
    vibracao da mesa) mais alto que o limiar era tomado por sync ou por tom -
    num teste real ela devolvia o inicio do sync em vez do inicio dos dados.

    Os limiares sao relativos ao ruido medido no proprio buffer (percentil
    10 do envelope de cada faixa), nao a um valor fixo.
    """
    signal = np.asarray(signal, dtype=np.float64)
    if len(signal) < CHUNK or not np.any(signal):
        return None

    env_s, win = _envelope(_filtrar(signal, fsync - 60, fsync + 60))
    env_d, _ = _envelope(_filtrar(signal, fbase - 150, fbase + 15 * step_hz + 150))
    n = len(env_s)

    # 1. o sync: o primeiro trecho em que a faixa do sync fica alta por pelo
    # menos 30% de syncDur. Nao o pico maximo - um estalo ou uma rajada de
    # ruido pode ser mais forte que o sync e esta em outro lugar.
    ruido_s, pico_s = np.percentile(env_s, 10), env_s.max()
    if pico_s < max(TOM_MINIMO, 4 * ruido_s):
        return None
    alto_s = env_s >= ruido_s + 0.3 * (pico_s - ruido_s)
    minimo = max(1, int(0.3 * syncDur * Fs / win))
    i, fim_sync = 0, None
    while i < n:
        if not alto_s[i]:
            i += 1
            continue
        j = i
        while j < n and alto_s[j]:
            j += 1
        if j - i >= minimo:
            fim_sync = j
            break
        i = j
    if fim_sync is None or fim_sync >= n:
        return None
    i = fim_sync

    # 2. o primeiro tom de dado depois do sync
    ruido_d, pico_d = np.percentile(env_d, 10), env_d[fim_sync:].max()
    if pico_d < max(TOM_MINIMO, 4 * ruido_d):
        return None
    alto = ruido_d + 0.10 * (pico_d - ruido_d)
    baixo = ruido_d + 0.02 * (pico_d - ruido_d)
    while i < n and env_d[i] < alto:
        i += 1
    if i >= n:
        return None

    # A janela Blackman faz o tom subir devagar, entao o cruzamento do limiar
    # acontece depois do inicio real. Recua ate o pe da subida.
    while i > fim_sync and env_d[i - 1] >= baixo:
        i -= 1

    # 3. Refina pela grade inteira. Com ruido alto, o limiar pode cruzar num
    # pico de ruido antes do primeiro tom; somar a energia de TODOS os tons
    # para cada deslocamento candidato acha o alinhamento mesmo assim. Se o
    # limiar caiu longe de onde os dados deveriam estar (fim do sync +
    # MIDGAP), a busca parte da posicao prevista.
    passo_w = int(round((dur + gap) * Fs / win))
    tom_w = int(round(dur * Fs / win))
    previsto = fim_sync + int(round(MIDGAP * Fs / win))
    centro = i if abs(i - previsto) <= passo_w // 2 else previsto
    lo, hi = max(fim_sync, centro - passo_w // 2), centro + passo_w // 2
    tons = (n - hi - tom_w) // passo_w + 1
    if tons >= 1 and hi > lo:
        acum = np.concatenate(([0.0], np.cumsum(env_d ** 2)))
        inicios = np.arange(lo, hi)[:, None] + passo_w * np.arange(tons)[None, :]
        energia = (acum[inicios + tom_w] - acum[inicios]).sum(axis=1)
        i = lo + int(np.argmax(energia))
    return i * win


def dominant_freq(segment):
    """Frequencia do maior pico do espectro inteiro. Mantida por
    compatibilidade - o decodificador usa _tom_na_faixa."""
    ft = np.abs(np.fft.rfft(segment * np.blackman(len(segment))))
    return np.argmax(ft) * Fs / len(segment)


def _proeminencia(amp, k0, k1, n):
    """(indice, amplitude, proeminencia) do pico mais destacado em amp[k0:k1].

    Proeminencia = amplitude / mediana dos vizinhos (JANELA_RUIDO_HZ em volta).
    Tem que ser LOCAL: ruido de sala nao e plano, e num teste real ele era
    ~20 dB mais forte em 700-1100 Hz do que em 3 kHz. Comparado com a mediana
    da faixa inteira, esse ruido parecia um tom.
    """
    lado = max(8, int(JANELA_RUIDO_HZ / 2 * n / Fs))
    a0, a1 = max(0, k0 - lado), min(len(amp), k1 + lado)
    trecho = amp[a0:a1]
    ref = median_filter(trecho, size=2 * lado + 1, mode='nearest')[k0 - a0:k1 - a0]
    faixa = amp[k0:k1]
    with np.errstate(divide='ignore', invalid='ignore'):
        prom = np.where(ref > 0, faixa / np.where(ref > 0, ref, 1), np.where(faixa > 0, np.inf, 0.0))
    k = int(np.argmax(prom))
    return k0 + k, faixa[k], prom[k]


def _tom_na_faixa(segment):
    """(frequencia, amplitude, proeminencia) do maior pico DENTRO da faixa
    dos dados.

    A versao anterior pegava o maior pico do espectro inteiro. Num teste real
    entre dois PCs, a vibracao do alto-falante na mesa gerava um componente em
    ~13 Hz mais forte que os tons, e 6 de 32 digitos foram lidos como lixo
    mesmo com o tom chegando 40-60 dB acima do ruido.
    """
    n = len(segment)
    w = np.blackman(n)
    amp = np.abs(np.fft.rfft(segment * w)) * 2 / w.sum()
    k0 = int(np.ceil((fbase - step_hz) * n / Fs))
    k1 = int((fbase + 16 * step_hz) * n / Fs) + 1
    k, pico, prom = _proeminencia(amp, k0, k1, n)
    return k * Fs / n, pico, prom


# ------------------------------------------------------------ decodificacao

def signal_to_hex(signal, verbose=False):
    """Vetor de audio -> string hexadecimal.

    Funciona tanto num sinal gerado inteiro quanto num buffer gravado que
    comeca no meio do sync, porque o inicio dos dados e procurado, nao assumido.
    """
    signal = np.asarray(signal, dtype=np.float64)
    inicio = find_data_start(signal)
    if inicio is None:
        return ''

    passo = int((dur + gap) * Fs)
    # Le so os 60% centrais de cada tom: sobra folga dos dois lados, entao um
    # erro de alinhamento de ate ~20% da nota ainda decodifica certo.
    recuo = int(0.20 * dur * Fs)
    largura = int(0.60 * dur * Fs)

    leituras = []
    for k in range((len(signal) - inicio - recuo - largura) // passo + 1):
        a = inicio + k * passo + recuo
        leituras.append(_tom_na_faixa(signal[a:a + largura]))

    # Uma janela so conta como digito se tiver um tom que se destaca na faixa.
    # O corte de amplitude e relativo ao tom mais forte (-30 dB), para pular
    # o silencio depois da mensagem sem depender do volume da gravacao.
    fortes = [amp for _, amp, prom in leituras if prom >= PROEMINENCIA]
    corte = max(TOM_MINIMO, 0.03 * max(fortes)) if fortes else np.inf

    digitos = []
    for freq, amp, prom in leituras:
        if prom < PROEMINENCIA or amp < corte:
            continue
        val = int(round((freq - fbase) / step_hz))
        if 0 <= val <= 15:
            digitos.append(format(val, 'x'))

    hexstr = ''.join(digitos)
    if verbose:
        print(f"Hex recebido: {hexstr}")
    return hexstr


def signal_to_bytes(signal, verbose=False):
    hexstr = signal_to_hex(signal, verbose=verbose)
    if len(hexstr) % 2:
        hexstr = hexstr[:-1]
    if not hexstr:
        return b''
    return binascii.unhexlify(hexstr)


def signal_to_text(signal, verbose=False):
    return signal_to_bytes(signal, verbose=verbose).decode('utf-8', errors='ignore')


def writeSignalToFile(signal, outputFile='decoded_out'):
    texto = signal_to_text(signal, verbose=True)
    with open(outputFile, 'w', encoding='utf-8') as f:
        f.write(texto)
    print(f"TEXTO: {texto}")
    return texto


# ------------------------------------------------------------------ canal

_JANELAS = {}


def analisar_bloco(bloco):
    """Classifica um bloco do microfone: (tem_tom, eh_sync, freq_do_pico).

    tem_tom: algum tom se destaca na faixa do protocolo (sync ate o digito f).
    eh_sync: e o tom mais forte da faixa esta na frequencia do sync.

    Graves abaixo da faixa (ventoinha, vibracao, ruido de manuseio) ficam de
    fora por construcao. A versao anterior aceitava o maior pico acima de
    ~940 Hz sem exigir que ele se destacasse, e ruido de sala comum - mais
    forte nos graves - tinha o pico logo ali: num teste real, 61% dos blocos
    de puro ruido eram tomados por sync.
    """
    bloco = np.asarray(bloco, dtype=np.float64)
    n = len(bloco)
    w = _JANELAS.get(n)
    if w is None:
        w = _JANELAS[n] = np.hanning(n)
    amp = np.abs(np.fft.rfft(bloco * w)) * 2 / w.sum()
    k0 = int(np.ceil(max(50, fsync - 300) * n / Fs))
    k1 = min(len(amp), int((fbase + 16 * step_hz + 300) * n / Fs) + 1)
    k, pico, prom = _proeminencia(amp, k0, k1, n)
    freq = k * Fs / n
    tem_tom = pico >= TOM_MINIMO and prom >= PROEMINENCIA
    eh_sync = tem_tom and abs(freq - fsync) <= 2 * Fs / n
    return tem_tom, eh_sync, freq


def _coletar(blocos, verbose=True, max_s=MAX_RECORD_S, info=None):
    """Nucleo do receptor: espera o sync, acumula ate o silencio final.

    Recebe um iteravel de blocos de int16 - venham eles do microfone ou de
    um array em memoria. E o que permite que gravar() e loopback() exercitem
    exatamente o mesmo caminho de codigo.

    O sync so conta depois de se sustentar por SYNC_MIN_S: um bloco isolado
    (uma palavra, um estalo) nao dispara a gravacao. Como a confirmacao chega
    atrasada, os blocos anteriores (PRE_ROLL_S) entram na gravacao, para que
    o inicio do sync nao se perca.

    "Silencio", para o fim da recepcao, e a ausencia de tom do protocolo - nao
    amplitude baixa. Assim o ruido de fundo da sala, por mais alto que seja,
    nao impede a recepcao de terminar.

    max_s e uma valvula de seguranca: no microfone, impede gravar para sempre
    se a transmissao nunca terminar. Ela tambem limita o payload maximo a
    cerca de 35 bytes por transmissao no valor padrao - suba se precisar de
    mensagens maiores.

    info: se for um dict, recebe ini/fim (em amostras desde o primeiro bloco)
    e o motivo de parada. Usado pela ferramenta de diagnostico.
    """
    n_sync = max(1, int(round(min(SYNC_MIN_S, 0.4 * syncDur) * Fs / CHUNK)))
    historico = deque(maxlen=n_sync + int(PRE_ROLL_S * Fs / CHUNK))
    limite_silencio = (Fs / CHUNK) * FIM_SILENCIO
    coletado, gravando, silencio, seguidos = [], False, 0, 0
    total = lidas = 0
    ini, motivo = None, 'fim do audio'

    for bloco in blocos:
        lidas += len(bloco)
        tem_tom, eh_sync, freq = analisar_bloco(bloco)

        if not gravando:
            historico.append(bloco)
            seguidos = seguidos + 1 if eh_sync else 0
            if seguidos >= n_sync:
                gravando = True
                coletado = list(historico)
                total = sum(len(b) for b in coletado)
                ini = lidas - total
                if verbose:
                    print(f"--> Sincronia detectada ({int(freq)} Hz)")
            continue

        coletado.append(bloco)
        total += len(bloco)
        if total > max_s * Fs:
            motivo = f'teto de {max_s}s'
            if verbose:
                print(f"Teto de {max_s}s atingido - o resto foi truncado.")
            break
        silencio = 0 if tem_tom else silencio + 1
        if silencio > limite_silencio:
            motivo = 'silencio final'
            if verbose:
                print("Fim da transmissao.")
            break

    if info is not None:
        info.update(ini=ini, fim=lidas, motivo=motivo if gravando else 'sync nao detectado')
    return np.concatenate(coletado) if coletado else np.zeros(0)


def _blocos_do_array(signal, chunk=CHUNK):
    for i in range(0, len(signal) - chunk + 1, chunk):
        yield signal[i:i + chunk]


def loopback(signal, ruido_db=None, verbose=False, rng=None, max_s=MAX_RECORD_S,
             ambiente_s=3.0):
    """Passa o sinal pelo receptor inteiro sem tocar em hardware.

    ruido_db:   se informado, injeta ruido branco nessa relacao sinal/ruido
                (em dB) antes de decodificar. Menor = pior canal.
    max_s:      mesmo teto de gravacao do microfone. Mantido igual de proposito,
                para que o loopback minta o menos possivel sobre o modo real.
    ambiente_s: silencio acrescentado no fim. No mundo real o microfone continua
                ouvindo depois do audio acabar, e e esse silencio que encerra a
                recepcao. Sem ele o array simplesmente terminaria e a deteccao
                de fim nunca seria exercitada.
    """
    signal = normalize_audio(np.asarray(signal, dtype=np.float64))
    if ambiente_s:
        signal = np.concatenate((signal, np.zeros(int(ambiente_s * Fs), dtype=np.int16)))
    if ruido_db is not None:
        signal = adicionar_ruido(signal, ruido_db, rng=rng)
    return _coletar(_blocos_do_array(signal), verbose=verbose, max_s=max_s)


def adicionar_ruido(signal, snr_db, rng=None):
    """Ruido branco na relacao sinal/ruido pedida, medida contra a amplitude
    do TOM - nao contra o RMS do arquivo.

    Usar o RMS do arquivo seria enganoso: um arquivo com mais silencio tem RMS
    menor e receberia menos ruido para a mesma SNR nominal. Perfis de
    temporizacao diferentes tem proporcoes de silencio diferentes, entao a
    comparacao entre eles ficaria sem sentido. A amplitude do tom e constante
    depois da normalizacao, o que da uma referencia estavel.
    """
    rng = rng or np.random.default_rng()
    tom_rms = 18000 / np.sqrt(2)
    ruido_rms = tom_rms / (10 ** (snr_db / 20))
    ruidoso = signal + rng.normal(0, ruido_rms, len(signal))
    return np.clip(ruidoso, -32768, 32767).astype(np.int16)


def gravar(verbose=True):
    """Escuta o microfone ate receber uma transmissao completa."""
    sd = _sounddevice()
    stream = sd.InputStream(samplerate=Fs, channels=1, dtype='int16', blocksize=CHUNK)
    stream.start()
    if verbose:
        print("Ouvindo...")

    def blocos():
        while True:
            try:
                dados, _overflow = stream.read(CHUNK)
                yield dados[:, 0]
            except KeyboardInterrupt:
                return

    try:
        return _coletar(blocos(), verbose=verbose)
    finally:
        stream.stop()
        stream.close()


def tocar(signal):
    """Toca o sinal pelo alto-falante."""
    sd = _sounddevice()
    dados = normalize_audio(np.asarray(signal, dtype=np.float64))
    sd.play(dados, samplerate=Fs, blocking=True)


def enviar_texto(texto):
    tocar(text_to_signal(texto))


def receber_texto(verbose=True):
    return signal_to_text(gravar(verbose=verbose))


def _sounddevice():
    """Importa sounddevice so na hora de usar, para que loopback, testes e o modo
    demo do jogo funcionem em maquina sem sounddevice instalado."""
    try:
        import sounddevice
        return sounddevice
    except ImportError:
        raise RuntimeError(
            "sounddevice nao esta instalado - so o modo audio precisa dele.\n"
            "  pip install sounddevice\n"
            "Para rodar sem hardware, use o modo loopback."
        )


# Compatibilidade com a v1/v2
record = gravar

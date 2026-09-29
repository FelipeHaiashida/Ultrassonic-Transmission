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
import numpy as np
from scipy.io import wavfile

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
SILENCE_FLOOR = 150     # limiar bruto de silencio, em int16
CHUNK         = 1024    # tamanho do bloco de leitura do microfone
MAX_RECORD_S  = 30      # teto de gravacao, em segundos


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


def find_data_start(signal):
    """Devolve o indice do primeiro tom de dado.

    Percorre a estrutura conhecida do sinal:
        [silencio] -> [sync tocando] -> [silencio] -> [primeiro tom de dado]
                                                       ^ retorna aqui

    A versao anterior assumia um offset fixo de 2.0s depois do sync. Isso so
    funcionava se a deteccao do sync caisse exatamente no inicio do tom - e
    qualquer desvio de dezenas de milissegundos desalinhava todos os tons
    seguintes. Procurar o inicio real resolve isso.
    """
    env, win = _envelope(signal)
    if len(env) == 0:
        return None

    pico = env.max()
    if pico <= 0:
        return None

    alto = max(SILENCE_FLOOR, 0.10 * pico)
    baixo = max(SILENCE_FLOOR, 0.02 * pico)
    n, i = len(env), 0

    while i < n and env[i] < alto:   # 1. espera o sync ficar audivel
        i += 1
    while i < n and env[i] >= alto:  # 2. espera o sync acabar
        i += 1
    while i < n and env[i] < alto:   # 3. espera o primeiro tom de dado
        i += 1
    if i >= n:
        return None

    # A janela Blackman faz o tom subir devagar, entao o cruzamento do limiar
    # acontece depois do inicio real. Recua ate o pe da subida.
    while i > 0 and env[i - 1] >= baixo:
        i -= 1
    return i * win


def dominant_freq(segment):
    ft = np.abs(np.fft.rfft(segment * np.blackman(len(segment))))
    return np.argmax(ft) * Fs / len(segment)


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

    pico = np.max(np.abs(signal))
    limiar = max(SILENCE_FLOOR, 0.05 * pico)

    digitos, k = [], 0
    while True:
        a = inicio + k * passo + recuo
        b = a + largura
        k += 1
        if b > len(signal):
            break

        seg = signal[a:b]
        if np.max(np.abs(seg)) < limiar:
            continue

        freq = dominant_freq(seg)
        if not (fbase - 500 < freq < fbase + 16 * step_hz + 500):
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

def _coletar(blocos, verbose=True, max_s=MAX_RECORD_S):
    """Nucleo do receptor: espera o sync, acumula ate o silencio final.

    Recebe um iteravel de blocos de int16 - venham eles do microfone ou de
    um array em memoria. E o que permite que gravar() e loopback() exercitem
    exatamente o mesmo caminho de codigo.

    max_s e uma valvula de seguranca: no microfone, impede gravar para sempre
    se a transmissao nunca terminar. Ela tambem limita o payload maximo a
    cerca de 35 bytes por transmissao no valor padrao - suba se precisar de
    mensagens maiores.
    """
    coletado, gravando, silencio = [], False, 0
    total = 0
    limite_silencio = (Fs / CHUNK) * FIM_SILENCIO

    for bloco in blocos:
        if not gravando:
            ft = np.abs(np.fft.rfft(bloco))
            ft[:20] = 0
            pico_hz = np.argmax(ft) * Fs / len(bloco)
            if fsync - 300 < pico_hz < fsync + 300:
                gravando = True
                if verbose:
                    print(f"--> Sincronia detectada ({int(pico_hz)} Hz)")

        if gravando:
            coletado.append(bloco)
            total += len(bloco)
            if total > max_s * Fs:
                if verbose:
                    print(f"Teto de {max_s}s atingido - o resto foi truncado.")
                break
            silencio = silencio + 1 if np.max(np.abs(bloco)) < SILENCE_FLOOR else 0
            if silencio > limite_silencio:
                if verbose:
                    print("Fim da transmissao.")
                break

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

"""
transfer_lib v5 - transmissao de dados por audio, desenhada para sala com ruido.

A v4 mostrou, numa sala de verdade, onde o protocolo antigo era fragil. Cada
decisao desta versao responde a uma dessas fragilidades:

  Faixa 5-8 kHz (v4: 1-3,5 kHz)
      O ruido de sala (voz, ventoinha, TV, passos) fica quase todo abaixo de
      4 kHz. Medido numa gravacao real, o piso em 1-3,5 kHz estava ~25 dB
      acima do piso de 4,5 kHz para cima. Os 16 tons ficam em 5000-8000 Hz,
      sem passar de uma oitava (8000 < 2 x 5000): o harmonico de um tom
      distorcido pelo alto-falante ou pelo microfone nunca cai em outro
      digito. Os tons tem 200 Hz de distancia (v4: 100 Hz), mais folga contra
      ressonancias da sala e erro de clock entre as duas placas de som.

  Sincronismo por chirp, nao por tom (v4: tom de 1 kHz)
      Um tom unico, justamente na faixa mais barulhenta, era achado por
      limiar de envelope - quebradico. Agora o preambulo e uma varredura de
      4,4 a 8,6 kHz, detectada por correlacao com o sinal conhecido. A
      correlacao concentra a energia da varredura inteira num pico estreito:
      acha o sinal mesmo bem abaixo do ruido e da o instante exato (sem
      "erro de +50 ms"). Um chirp descendente no fim marca onde a mensagem
      acaba, o que dispensa adivinhar o fim por silencio e ainda mede a deriva
      de clock entre emissor e receptor.

  Reed-Solomon com leitura suave (v4: nenhuma correcao)
      Um tom lido errado corrompia o byte inteiro. Agora cada mensagem leva
      bytes de paridade, e o receptor, em vez de apostar num valor quando um
      tom soa ambiguo, marca o byte como apagamento - que a paridade corrige
      pela metade do preco de um erro.

  Dados embaralhados
      Os bytes sao combinados (XOR) com uma sequencia pseudoaleatoria antes de
      virar tom. Sem isso, "0000" seria o mesmo tom repetido, e um tom que a
      sala atenua (uma ressonancia, um nulo de interferencia) falharia sempre
      que aparecesse. Embaralhado, o dano se espalha por bytes diferentes.

  Falha explicita
      Se a paridade nao consegue reparar, o resultado e vazio - nunca lixo.

Estrutura do sinal:

    [WARMUP] [chirp ^] [GUARD] [dado][gap][dado][gap]... [GUARD] [chirp v] [COOLDOWN]

Cada byte da palavra-codigo vira dois tons (um por nibble). O numero de tons
lidos entre os dois chirps diz o tamanho da palavra-codigo, e dele sai o
tamanho do payload.

Duas formas de usar o canal:

    real      tocar(sinal) / gravar()     precisa de sounddevice
    loopback  loopback(sinal)             so numpy/scipy, roda em qualquer maquina

O loopback passa pelo MESMO detector e pelo MESMO decodificador do modo real.
"""

import math
from collections import deque

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, fftconvolve, sosfilt, sosfiltfilt
from scipy.signal.windows import tukey

import reed_solomon as rs

# --- Parametros do canal ---
Fs       = 48000     # taxa de amostragem
fbase    = 5000      # frequencia do digito hex 0
step_hz  = 200       # distancia entre digitos (0 -> 5000 Hz ... f -> 8000 Hz)
dur      = 0.20      # duracao de cada tom de dado
gap      = 0.06      # silencio depois de cada tom

CHIRP_F0  = 4400     # varredura do preambulo: sobe de F0 a F1,
CHIRP_F1  = 8600     # a do fim desce de F1 a F0
CHIRP_DUR = 0.6      # chirps longos: mais energia = mais folga contra ruido

WARMUP   = 1.0       # silencio inicial (a placa de som precisa acordar)
GUARD    = 0.15      # silencio entre chirp e dados, nos dois lados
COOLDOWN = 0.4       # silencio no fim

NIVEL_TX = 0.80      # pico do sinal emitido, fracao do fundo de escala. Os
                     # sinais sao de envoltoria constante (so um tom ou um
                     # chirp por vez), entao aguentam mais volume que o de
                     # v4 sem o aumento de crista que distorce

# Correcao de erro: quantos bytes de paridade para um payload de p bytes.
NSYM_MIN = 4
PARIDADE = 0.5       # paridade = max(NSYM_MIN, ceil(p * PARIDADE))
VERIFICACAO_MIN = 4  # paridade que tem de sobrar, depois dos apagamentos, para verificar o resultado
MAX_PAYLOAD = 170    # p + paridade tem que caber em 255 (limite do RS sobre GF(256))

# Leitura dos tons
LEITURA_DE  = 0.25   # le o tom a partir de 25% da duracao: o eco do tom anterior
                     # (reverberacao) ja decaiu mais nessa altura
LEITURA_ATE = 1.00

# Deteccao do chirp. rho e a correlacao normalizada, de 0 (nada a ver) a 1
# (identico). Ruido puro, medido (branco, rosa, sala real +20 dB, rajadas),
# nunca passou de 0,10. Nenhum limiar de volume fixo: rho ignora o volume.
RHO_DISPARO  = 0.15  # para acordar o receptor (streaming)
RHO_MINIMO   = 0.08  # para alinhar uma gravacao que ja se sabe ter mensagem
RMS_MIN_RX   = 0.5   # abaixo disto (em unidades de int16) a janela e silencio digital
CHECK_BLOCOS = 8     # a cada quantos blocos de microfone procura chirp
PRE_ROLL_S   = 0.3
MAX_RECORD_S = 200   # teto de gravacao; protege contra disparo falso sem fim

CHUNK = 1024         # tamanho do bloco de leitura do microfone

# Banda do receptor: so o que o protocolo usa (com folga). Graves, vibracao e
# ruido de manuseio ficam de fora por construcao.
BANDA_RX = (4200, 8800)

# Perfis de temporizacao. Emissor e receptor precisam usar o MESMO perfil.
PERFIS = {
    # Robusto: para sala barulhenta, hardware desconhecido, distancia.
    'padrao': dict(dur=0.20, gap=0.06, CHIRP_DUR=0.6, WARMUP=1.0, GUARD=0.15,
                   COOLDOWN=0.4, PARIDADE=0.5),
    # Trocas curtas com o receptor ja escutando, sala razoavel. Mais veloz,
    # menos margem: mede-se com comparar_v4.py.
    'jogo':   dict(dur=0.12, gap=0.04, CHIRP_DUR=0.4, WARMUP=0.5, GUARD=0.10,
                   COOLDOWN=0.25, PARIDADE=0.5),
}


def usar_perfil(nome):
    """Troca a temporizacao (emissor e receptor tem que usar o mesmo)."""
    if nome not in PERFIS:
        raise ValueError(f"perfil desconhecido: {nome!r}. Use um de {list(PERFIS)}")
    globals().update(PERFIS[nome])
    return nome


def preambulo_s():
    """Segundos gastos por transmissao independente do tamanho do payload."""
    return WARMUP + 2 * CHIRP_DUR + 2 * GUARD + COOLDOWN


def tempo_s(n_bytes):
    """Duracao total, em segundos, de uma transmissao de n_bytes de payload."""
    return preambulo_s() + 2 * _n_palavra(n_bytes) * (dur + gap)


# ------------------------------------------------------------------ framing

def _nsym(p):
    return max(NSYM_MIN, math.ceil(p * PARIDADE))


def _n_palavra(p):
    return p + _nsym(p)


def _payload_para_n(n):
    """Inverso de _n_palavra, ou None se n nao corresponde a nenhum payload."""
    for p in range(0, MAX_PAYLOAD + 1):
        if _n_palavra(p) == n:
            return p
        if _n_palavra(p) > n:
            return None
    return None


def _embaralhar(dados, n):
    """XOR com uma sequencia pseudoaleatoria fixa (LCG de 31 bits). Aplicar
    duas vezes devolve o original."""
    x, saida = 0x1F2E3D4, []
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        saida.append(dados[i] ^ ((x >> 16) & 0xFF))
    return saida


def _bytes_para_nibbles(palavra):
    nib = []
    for b in palavra:
        nib += [b >> 4, b & 0x0F]
    return nib


# ---------------------------------------------------------------- codificacao

def _chirp(f0, f1, duracao):
    n = int(round(duracao * Fs))
    t = np.arange(n) / Fs
    k = (f1 - f0) / duracao
    return tukey(n, 0.2) * np.sin(2 * np.pi * (f0 * t + 0.5 * k * t * t))


def _chirp_sobe():
    return _chirp(CHIRP_F0, CHIRP_F1, CHIRP_DUR)


def _chirp_desce():
    return _chirp(CHIRP_F1, CHIRP_F0, CHIRP_DUR)


def _tom(f):
    n = int(round(dur * Fs))
    t = np.arange(n) / Fs
    return tukey(n, 0.2) * np.sin(2 * np.pi * f * t)


def _passo():
    """Amostras por simbolo (tom + silencio)."""
    return int(round((dur + gap) * Fs))


def hex_to_signal(hexstr):
    """String hexadecimal -> audio (float). Os bytes viram payload."""
    return bytes_to_signal(bytes.fromhex(hexstr))


def bytes_to_signal(data):
    data = bytes(data)
    if len(data) > MAX_PAYLOAD:
        raise ValueError(f"payload de {len(data)} bytes passa do maximo de "
                         f"{MAX_PAYLOAD} por transmissao - divida em varias")
    palavra = rs.codificar(data, _nsym(len(data)))
    palavra = _embaralhar(palavra, len(palavra))

    passo = _passo()
    slots = []
    for v in _bytes_para_nibbles(palavra):
        s = np.zeros(passo)
        tom = _tom(fbase + step_hz * v)
        s[:len(tom)] = tom
        slots.append(s)

    return np.concatenate((
        np.zeros(int(WARMUP * Fs)),
        _chirp_sobe(),
        np.zeros(int(GUARD * Fs)),
        *slots,
        np.zeros(int(GUARD * Fs)),
        _chirp_desce(),
        np.zeros(int(COOLDOWN * Fs)),
    ))


def text_to_signal(texto):
    return bytes_to_signal(texto.encode('utf-8'))


def normalize_audio(audio_data):
    """Ajusta o pico para NIVEL_TX do fundo de escala e converte para int16."""
    m = np.max(np.abs(audio_data))
    if m == 0:
        return np.zeros(len(audio_data), dtype=np.int16)
    return ((audio_data / m) * NIVEL_TX * 32767).astype(np.int16)


def write_wav(signal, outputFile='output.wav'):
    wavfile.write(outputFile, Fs, normalize_audio(signal))
    return outputFile


def writeFileToWav(filename, outputFile='output.wav'):
    with open(filename, 'rb') as f:
        dados = f.read()
    write_wav(bytes_to_signal(dados), outputFile)
    return True


# ---------------------------------------------------------------- alinhamento

_SOS_RX = {}


def _sos():
    chave = (Fs, BANDA_RX)
    if chave not in _SOS_RX:
        _SOS_RX[chave] = butter(6, BANDA_RX, btype='bandpass', fs=Fs, output='sos')
    return _SOS_RX[chave]


def _rho(x, molde):
    """Correlacao normalizada de x com o molde, para cada alinhamento em que o
    molde cabe inteiro. rho[m] = quanto x[m : m+len(molde)] parece o molde,
    de 0 a 1, ignorando o volume (divide pela energia da propria janela)."""
    n = len(molde)
    if len(x) < n:
        return np.zeros(0)
    c = fftconvolve(x, molde[::-1], mode='valid')
    energia = np.concatenate(([0.0], np.cumsum(x * x)))
    janela = energia[n:] - energia[:len(energia) - n]
    # Janela sem sinal nenhum (silencio digital, microfone com noise gate) tem
    # energia zero so no papel: a soma acumulada deixa um residuo de
    # arredondamento, e dividir lixo por lixo dava rho de 5,96 - maior que o
    # maximo possivel, 1. Abaixo de RMS_MIN_RX nao ha o que correlacionar.
    valida = janela > n * RMS_MIN_RX ** 2
    rho = np.zeros(len(c))
    rho[valida] = np.abs(c[valida]) / (np.linalg.norm(molde) * np.sqrt(janela[valida]))
    return rho


def _achar_chirp(x, molde, de=0, ate=None, minimo=RHO_MINIMO):
    """(indice_de_inicio, rho) do melhor alinhamento de molde em x[de:ate],
    ou None se nenhum passar de `minimo`."""
    ate = len(x) if ate is None else ate
    r = _rho(x[de:ate], molde)
    if len(r) == 0:
        return None
    m = int(np.argmax(r))
    if r[m] < minimo:
        return None
    return de + m, float(r[m])


def _alinhar(y):
    """Acha os dois chirps em y (sinal ja filtrado) e devolve
    (inicio_dos_dados, n_simbolos, escala, rho_sobe, rho_desce), ou None.

    escala e a razao entre o tempo medido entre os chirps e o esperado: 1,0001
    significa que o clock do emissor corre 0,01% mais devagar que o do
    receptor. E usada para esticar a grade de leitura dos tons.
    """
    sobe, desce = _chirp_sobe(), _chirp_desce()
    a = _achar_chirp(y, sobe)
    if a is None:
        return None
    ini, rho_a = a
    dados = ini + len(sobe) + int(GUARD * Fs)
    b = _achar_chirp(y, desce, de=dados)
    if b is None:
        return None
    fim, rho_b = b

    passo = _passo()
    medido = fim - int(GUARD * Fs) - dados
    n_simbolos = int(round(medido / passo))
    if n_simbolos < 2 * NSYM_MIN or n_simbolos % 2:
        return None
    escala = medido / (n_simbolos * passo)
    if abs(escala - 1) > 0.01:      # clocks nao diferem em 1%: foi um alinhamento falso
        return None
    return dados, n_simbolos, escala, rho_a, rho_b


def _energias(y, dados, n_simbolos, escala):
    """Energia de cada um dos 16 tons candidatos em cada simbolo.

    Devolve array (n_simbolos, 16). E um DFT direto nas 16 frequencias
    conhecidas (com janela de Hann), mais seletivo e barato que uma FFT
    inteira: o que esta fora dos 16 tons nem entra na conta.
    """
    passo = _passo()
    a = int(LEITURA_DE * dur * Fs)
    b = int(LEITURA_ATE * dur * Fs)
    n = b - a
    t = np.arange(n) / Fs
    freqs = fbase + step_hz * np.arange(16)
    base = np.exp(-2j * np.pi * freqs[None, :] * t[:, None])      # (n, 16)
    janela = np.hanning(n)[:, None]

    energias = np.zeros((n_simbolos, 16))
    for k in range(n_simbolos):
        ini = int(round(dados + k * passo * escala)) + a
        seg = y[ini:ini + n]
        if len(seg) < n:
            seg = np.pad(seg, (0, n - len(seg)))
        energias[k] = np.abs((seg[:, None] * janela * base).sum(axis=0)) ** 2
    return energias


# -------------------------------------------------------------- decodificacao

def decodificar(signal):
    """Gravacao -> dict com o resultado e o que se mediu no caminho.

    Chaves: ok (bool), payload (bytes), motivo (str, se nao ok), e, se achou os
    chirps: rho_sobe, rho_desce, n_simbolos, escala, corrigidos (bytes que o
    Reed-Solomon consertou), apagados (bytes marcados como duvidosos).
    """
    x = np.asarray(signal, dtype=np.float64)
    if len(x) < Fs // 2 or not np.any(x):
        return dict(ok=False, payload=b'', motivo='sem sinal')
    y = sosfiltfilt(_sos(), x)

    al = _alinhar(y)
    if al is None:
        return dict(ok=False, payload=b'', motivo='chirps nao encontrados')
    dados, n_simbolos, escala, rho_a, rho_b = al
    info = dict(rho_sobe=rho_a, rho_desce=rho_b, n_simbolos=n_simbolos, escala=escala)

    n_bytes = n_simbolos // 2
    p = _payload_para_n(n_bytes)
    if p is None:
        return dict(ok=False, payload=b'', motivo=f'tamanho {n_bytes} B invalido', **info)
    nsym = _nsym(p)

    e = _energias(y, dados, n_simbolos, escala)
    ordem = np.argsort(e, axis=1)
    melhor = ordem[:, -1]
    segundo = ordem[:, -2]
    linhas = np.arange(n_simbolos)
    # Confianca de cada tom: quanto o vencedor se destaca do segundo colocado.
    # Perto de 1 = o ouvido duvida entre dois valores.
    conf = e[linhas, melhor] / (e[linhas, segundo] + 1e-12)

    nib = melhor.reshape(-1, 2)
    palavra = _embaralhar([int(h) << 4 | int(l) for h, l in nib], n_bytes)
    conf_byte = conf.reshape(-1, 2).min(axis=1)
    menos_confiaveis = np.argsort(conf_byte)

    # Tenta sem apagamentos; se a paridade nao der conta, vai apagando os
    # bytes menos confiaveis, um a mais por tentativa. Aceita a primeira que
    # fecha - o proprio Reed-Solomon recusa o que nao consegue verificar.
    # Mas so enquanto sobrarem VERIFICACAO_MIN bytes de paridade para
    # verificar: com k = nsym apagamentos o sistema fecha SEMPRE (qualquer
    # palavra vira "valida" depois de reescrever os k bytes), o que entregaria
    # lixo com cara de mensagem.
    for k in range(0, max(0, nsym - VERIFICACAO_MIN) + 1):
        try:
            msg, corrigidos = rs.decodificar(palavra, nsym, menos_confiaveis[:k])
        except rs.ErroRS:
            continue
        return dict(ok=True, payload=bytes(msg), corrigidos=corrigidos, apagados=k, **info)
    return dict(ok=False, payload=b'', motivo='dano alem da correcao', **info)


def signal_to_bytes(signal, verbose=False):
    r = decodificar(signal)
    if verbose:
        if r['ok']:
            print(f"Recebido: {len(r['payload'])} B  (rho {r['rho_sobe']:.2f}/{r['rho_desce']:.2f}, "
                  f"{r['corrigidos']} B corrigidos, {r['apagados']} apagados)")
        else:
            print(f"Nao decodificou: {r['motivo']}")
    return r['payload']


def signal_to_hex(signal, verbose=False):
    return signal_to_bytes(signal, verbose=verbose).hex()


def signal_to_text(signal, verbose=False):
    return signal_to_bytes(signal, verbose=verbose).decode('utf-8', errors='ignore')


def writeSignalToFile(signal, outputFile='decoded_out'):
    texto = signal_to_text(signal, verbose=True)
    with open(outputFile, 'w', encoding='utf-8') as f:
        f.write(texto)
    print(f"TEXTO: {texto}")
    return texto


# ------------------------------------------------------------------- canal

def _coletar(blocos, verbose=True, max_s=MAX_RECORD_S, info=None):
    """Nucleo do receptor: espera o chirp de inicio, acumula ate o chirp do fim.

    Recebe um iteravel de blocos de int16 - do microfone ou de um array. E o
    que permite que gravar() e loopback() exercitem o mesmo codigo.

    Cada bloco passa por um passa-faixa (a banda do protocolo) antes de ser
    comparado com os chirps, entao grave e vibracao nao escondem o sinal. A
    comparacao e a mesma correlacao normalizada do decodificador: nao ha
    limiar de volume em lugar nenhum.

    O fim e o chirp descendente, nao o silencio: ruido de fundo, por mais
    alto que seja, nao prolonga a gravacao. Se aparecer OUTRO chirp
    ascendente no meio da gravacao, o primeiro foi um disparo falso (ou a
    transmissao foi abandonada) e a gravacao recomeca a partir do novo.
    """
    sobe, desce = _chirp_sobe(), _chirp_desce()
    n_chirp = len(sobe)
    margem = int(0.005 * Fs)
    tamanho_cauda = n_chirp + int(0.5 * Fs)
    pre_roll = int(PRE_ROLL_S * Fs)

    sos = _sos()
    zi = np.zeros((sos.shape[0], 2))
    cauda_f = np.zeros(0)       # sinal filtrado recente
    cauda_f_ini = 0             # indice absoluto da primeira amostra de cauda_f
    historico = np.zeros(0)     # sinal cru recente (antes de disparar)
    hist_ini = 0
    coletado = []               # sinal cru (depois de disparar)
    n_coletado = 0
    gravando = False
    ini_abs = None
    lidas = 0
    n_bloco = 0
    motivo = 'sync nao detectado'

    for bloco in blocos:
        bloco = np.asarray(bloco, dtype=np.float64)
        filtrado, zi = sosfilt(sos, bloco, zi=zi)
        lidas += len(bloco)
        n_bloco += 1

        cauda_f = np.concatenate((cauda_f, filtrado))
        if len(cauda_f) > tamanho_cauda:
            cortar = len(cauda_f) - tamanho_cauda
            cauda_f, cauda_f_ini = cauda_f[cortar:], cauda_f_ini + cortar

        if not gravando:
            historico = np.concatenate((historico, bloco))
            limite = tamanho_cauda + pre_roll
            if len(historico) > limite:
                cortar = len(historico) - limite
                historico, hist_ini = historico[cortar:], hist_ini + cortar
        else:
            coletado.append(bloco)
            n_coletado += len(bloco)
            if n_coletado > max_s * Fs:
                motivo = f'teto de {max_s}s'
                if verbose:
                    print(f"Teto de {max_s}s atingido - sem chirp de fim.")
                break

        if n_bloco % CHECK_BLOCOS:
            continue

        if not gravando:
            r = _rho(cauda_f, sobe)
            if len(r) and r.max() >= RHO_DISPARO:
                m = int(np.argmax(r))
                if m <= len(r) - 1 - margem:      # o pico ja passou, nao e uma subida
                    ini_abs = cauda_f_ini + m
                    de = max(hist_ini, ini_abs - pre_roll)
                    coletado = [historico[de - hist_ini:]]
                    n_coletado = len(coletado[0])
                    gravando = True
                    if verbose:
                        print(f"--> Chirp de inicio detectado (rho {r[m]:.2f})")
            continue

        # gravando: procura o chirp de fim; se vier outro de inicio, recomeca
        r_fim = _rho(cauda_f, desce)
        if len(r_fim) and r_fim.max() >= RHO_DISPARO:
            m = int(np.argmax(r_fim))
            fim_abs = cauda_f_ini + m
            if m <= len(r_fim) - 1 - margem and fim_abs > ini_abs + 2 * n_chirp:
                motivo = 'chirp de fim'
                if verbose:
                    print("Fim da transmissao.")
                break
        r_ini = _rho(cauda_f, sobe)
        if len(r_ini) and r_ini.max() >= RHO_DISPARO:
            m = int(np.argmax(r_ini))
            novo = cauda_f_ini + m
            if m <= len(r_ini) - 1 - margem and novo > ini_abs + n_chirp:
                ini_abs = novo
                total = np.concatenate(coletado)
                fim_total = lidas
                comeco = fim_total - len(total)
                de = max(comeco, ini_abs - pre_roll)
                coletado = [total[de - comeco:]]
                n_coletado = len(coletado[0])
                if verbose:
                    print("--> Novo chirp de inicio: descartando o anterior.")

    if info is not None:
        info.update(ini=ini_abs, fim=lidas, motivo=motivo if gravando else 'sync nao detectado')
    if not gravando:
        return np.zeros(0)
    # um pouco de silencio depois do chirp de fim, para a filtragem de fase zero
    return np.concatenate(coletado)


def _blocos_do_array(signal, chunk=CHUNK):
    for i in range(0, len(signal) - chunk + 1, chunk):
        yield signal[i:i + chunk]


def loopback(signal, ruido_db=None, verbose=False, rng=None, max_s=MAX_RECORD_S,
             ambiente_s=1.0, canal=None):
    """Passa o sinal pelo receptor inteiro sem tocar em hardware.

    ruido_db:   se informado, injeta ruido branco nessa relacao sinal/ruido
                (em dB) antes de decodificar. Menor = pior canal.
    canal:      funcao int16[] -> int16[] que aplica qualquer outra deformacao
                (ruido de sala, reverb, interferencia...). Veja canal_sim.py.
    ambiente_s: silencio acrescentado no fim, como o microfone real, que segue
                ouvindo depois do audio acabar.
    """
    signal = normalize_audio(np.asarray(signal, dtype=np.float64))
    if ambiente_s:
        signal = np.concatenate((signal, np.zeros(int(ambiente_s * Fs), dtype=np.int16)))
    if canal is not None:
        signal = canal(signal)
    if ruido_db is not None:
        signal = adicionar_ruido(signal, ruido_db, rng=rng)
    return _coletar(_blocos_do_array(signal), verbose=verbose, max_s=max_s)


def adicionar_ruido(signal, snr_db, rng=None):
    """Ruido branco na relacao sinal/ruido pedida, medida contra a amplitude do
    TOM (nao contra o RMS do arquivo, que varia com a proporcao de silencio)."""
    rng = rng or np.random.default_rng()
    tom_rms = NIVEL_TX * 32767 / np.sqrt(2)
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


# Compatibilidade com versoes anteriores
record = gravar

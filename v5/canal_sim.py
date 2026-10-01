"""
Deformacoes de canal para testar o protocolo sem alto-falante e sem microfone.

Cada efeito e uma funcao  int16[] -> int16[]  que se passa a `tl.loopback(...,
canal=...)`. Os niveis sao relativos ao PICO do proprio sinal que entra, entao
os mesmos numeros valem para qualquer versao do protocolo.

    canal = compor(reverb(0.4), ruido_gravado('sala.wav', ganho_db=20),
                   interferente(6000, -10))
    tl.loopback(sinal, canal=canal)

Isto e uma simulacao: serve para comparar versoes e achar fraquezas, nao para
prometer o que o seu hardware vai entregar. O que ela nao tem: resposta em
frequencia do alto-falante e do microfone, controle automatico de ganho,
cancelamento de ruido do sistema, ecos de varias paredes.
"""

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.io import wavfile
from scipy.signal import butter, fftconvolve, sosfilt


def _int16(x):
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


def compor(*efeitos):
    def canal(x):
        for e in efeitos:
            x = e(x)
        return x
    return canal


def ruido_branco(snr_db, rng=None):
    """Ruido branco, snr_db contra o RMS de um tom no pico do sinal."""
    def f(x):
        r = rng or np.random.default_rng()
        pico = np.max(np.abs(x.astype(np.float64)))
        sigma = (pico / np.sqrt(2)) / 10 ** (snr_db / 20)
        return _int16(x + r.normal(0, sigma, len(x)))
    return f


def carregar_ruido(caminho):
    """Trechos de silencio de uma gravacao real (ex.: a do diagnostico), um
    atras do outro. Silencio = blocos de 0,25 s cujo pico fica abaixo de 2x o
    percentil 10 dos picos da gravacao (descarta os tons do teste)."""
    fs, x = wavfile.read(caminho)
    x = x.astype(np.float64)
    if x.ndim > 1:
        x = x[:, 0]
    w = int(0.25 * fs)
    n = len(x) // w
    blocos = x[:n * w].reshape(n, w)
    env = np.abs(blocos).max(axis=1)
    quieto = env < 2 * np.percentile(env, 10)
    return blocos[quieto].reshape(-1), fs


def ruido_gravado(caminho_ou_ruido, ganho_db=0.0, rng=None):
    """Ruido de uma sala de verdade, repetido com deslocamento aleatorio.

    ganho_db: 0 = no volume em que foi gravado, supondo o sinal no pico do
              fundo de escala; +20 = dez vezes mais alto.
    """
    if isinstance(caminho_ou_ruido, str):
        base, _ = carregar_ruido(caminho_ou_ruido)
    else:
        base = caminho_ou_ruido

    def f(x):
        r = rng or np.random.default_rng()
        reps = len(x) // len(base) + 2
        longo = np.tile(base, reps)
        ini = int(r.integers(0, len(base)))
        ruido = longo[ini:ini + len(x)] * 10 ** (ganho_db / 20)
        return _int16(x + ruido)
    return f


def ruido_colorido(rms, expoente=1.0, rng=None):
    """Ruido sintetico de sala: densidade ~ 1/f^expoente (1 = rosa, mais
    energia nos graves, como voz/ventoinha/TV). rms em unidades de int16."""
    def f(x):
        r = rng or np.random.default_rng()
        n = len(x)
        espectro = np.fft.rfft(r.normal(size=n))
        freqs = np.fft.rfftfreq(n, 1 / 48000)
        freqs[0] = freqs[1]
        espectro /= freqs ** (expoente / 2)
        ruido = np.fft.irfft(espectro, n)
        ruido *= rms / ruido.std()
        return _int16(x + ruido)
    return f


def reverb(rt60, mistura=0.6, rng=None):
    """Sala reverberante: soma uma cauda de ruido que decai 60 dB em rt60
    segundos. mistura = energia do eco em relacao ao som direto."""
    def f(x):
        r = rng or np.random.default_rng()
        n = int(1.2 * rt60 * 48000)
        t = np.arange(n) / 48000
        h = r.normal(size=n) * 10 ** (-3 * t / rt60)
        h /= np.sqrt((h ** 2).sum())
        h *= np.sqrt(mistura)
        h[0] += 1.0
        return _int16(fftconvolve(x.astype(np.float64), h)[:len(x)])
    return f


def interferente(freq, nivel_db, rng=None):
    """Tom continuo (apito de equipamento, alarme, zumbido) em `freq`, com
    nivel_db em relacao ao pico do sinal (-10 = 10 dB abaixo)."""
    def f(x):
        pico = np.max(np.abs(x.astype(np.float64)))
        t = np.arange(len(x)) / 48000
        fase = (rng or np.random.default_rng()).uniform(0, 2 * np.pi)
        return _int16(x + pico * 10 ** (nivel_db / 20) * np.sin(2 * np.pi * freq * t + fase))
    return f


def rajadas(quantidade, duracao_s, nivel_db, banda=(300, 9000), rng=None):
    """Rajadas curtas de ruido (porta batendo, palma, tosse, copo) em
    posicoes aleatorias. nivel_db em relacao ao pico do sinal."""
    def f(x):
        r = rng or np.random.default_rng()
        pico = np.max(np.abs(x.astype(np.float64)))
        sos = butter(4, banda, btype='bandpass', fs=48000, output='sos')
        y = x.astype(np.float64)
        n = int(duracao_s * 48000)
        for _ in range(quantidade):
            b = sosfilt(sos, r.normal(size=n))
            b *= np.hanning(n) * pico * 10 ** (nivel_db / 20) / b.std() / 3
            i = int(r.integers(0, max(1, len(y) - n)))
            y[i:i + n] += b
        return _int16(y)
    return f


def deriva(ppm):
    """Clocks diferentes de emissor e receptor: o audio chega esticado ou
    comprimido em `ppm` partes por milhao (placas de som reais: ate ~100)."""
    def f(x):
        n = len(x)
        t = np.arange(n) * (1 + ppm * 1e-6)
        t = t[t <= n - 1]
        return _int16(CubicSpline(np.arange(n), x.astype(np.float64))(t))
    return f


def saturar(ganho_db):
    """Microfone com ganho alto demais: amplifica e corta no fundo de escala."""
    def f(x):
        return _int16(x.astype(np.float64) * 10 ** (ganho_db / 20))
    return f


def silencio_antes(segundos):
    """Acrescenta silencio digital antes (o receptor ja estava ouvindo)."""
    def f(x):
        return np.concatenate((np.zeros(int(segundos * 48000), dtype=np.int16), x))
    return f

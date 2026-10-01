"""
Mede o que o SEU alto-falante e o SEU microfone entregam, tom a tom.

    python calibrar.py                 # toca e grava no mesmo PC (precisa de sounddevice)
    python calibrar.py --arquivo calibracao.wav   # reanalisa uma gravacao salva

A v5 usa 5-8 kHz porque o ruido de sala e bem menor ali do que em 1-3,5 kHz.
Mas isso so vale se o hardware reproduz e capta essa faixa: alto-falantes
pequenos e microfones de notebook as vezes cortam o agudo, e a ordem de
grandeza do corte so se descobre medindo.

O que faz: grava 2 s de silencio (o ruido do ambiente), toca um chirp largo
para alinhar, depois um tom a cada 500 Hz de 500 a 15000 Hz, gravando tudo ao
mesmo tempo. Para cada frequencia compara o nivel do tom com o ruido medido
naquela mesma frequencia - a SNR de verdade, que e o que decide se a faixa
serve. Fique em silencio durante a medicao e deixe o volume como vai usar.

Limite: alto-falante e microfone do MESMO PC, a poucos centimetros. Em dois PCs
separados por uma sala o nivel cai (e o ruido, em geral, nao), entao use a SNR
daqui como ordem de grandeza e as diferencas ENTRE faixas como o dado util.
"""

import argparse
import sys

import numpy as np
from scipy.io import wavfile

import transfer_lib as tl

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

FREQS      = list(range(500, 15001, 500))
SILENCIO_S = 2.0
TOM_S      = 0.4
GAP_S      = 0.1
CHIRP_LARGO = (500, 15000, 0.3)


def sinal_de_teste():
    """(sinal float, indice de inicio do primeiro tom a partir do inicio do chirp)."""
    chirp = tl._chirp(*CHIRP_LARGO)
    n_tom, n_gap = int(TOM_S * tl.Fs), int(GAP_S * tl.Fs)
    t = np.arange(n_tom) / tl.Fs
    janela = np.hanning(n_tom) ** 0.25          # sobe e desce suave, quase retangular
    partes = []
    for f in FREQS:
        partes += [janela * np.sin(2 * np.pi * f * t), np.zeros(n_gap)]
    guarda = int(0.2 * tl.Fs)
    sinal = np.concatenate((np.zeros(int(0.5 * tl.Fs)), chirp, np.zeros(guarda), *partes,
                            np.zeros(int(0.5 * tl.Fs))))
    return sinal, int(0.5 * tl.Fs), len(chirp) + guarda


def _nivel_db(seg, f):
    """Nivel (dB re 1 int16 de amplitude) da componente de frequencia f."""
    n = len(seg)
    w = np.hanning(n)
    t = np.arange(n) / tl.Fs
    amp = np.abs((seg * w * np.exp(-2j * np.pi * f * t)).sum()) * 2 / w.sum()
    return 20 * np.log10(amp + 1e-9)


def analisar(gravacao, n_silencio):
    """gravacao: audio gravado (int16) com n_silencio amostras de silencio no
    comeco. Devolve lista de (freq, nivel_dB, ruido_dB, snr_dB)."""
    x = np.asarray(gravacao, dtype=np.float64)
    ruido = x[:n_silencio]
    resto = x[n_silencio:]
    _sinal, ini_chirp, ate_tons = sinal_de_teste()
    molde = tl._chirp(*CHIRP_LARGO)
    achado = tl._achar_chirp(resto, molde, minimo=0.05)
    if achado is None:
        raise RuntimeError("Nao achei o chirp de alinhamento: o microfone nao ouviu o alto-falante "
                           "(volume, dispositivo errado ou muito longe).")
    inicio, rho = achado
    primeiro = inicio + ate_tons
    n_tom, passo = int(TOM_S * tl.Fs), int((TOM_S + GAP_S) * tl.Fs)

    saida = []
    for i, f in enumerate(FREQS):
        a = primeiro + i * passo + int(0.1 * n_tom)
        seg = resto[a:a + int(0.8 * n_tom)]
        if len(seg) < int(0.8 * n_tom):
            break
        lvl = _nivel_db(seg, f)
        # ruido na mesma frequencia: media de varios trechos do silencio, com a mesma janela
        tam = len(seg)
        ruidos = [_nivel_db(ruido[j:j + tam], f) for j in range(0, len(ruido) - tam, tam // 2)]
        r = 10 * np.log10(np.mean(10 ** (np.array(ruidos) / 10)))
        saida.append((f, lvl, r, lvl - r))
    return saida, rho


def relatorio(medidas):
    print(f"\n{'Hz':>6}  {'tom':>7}  {'ruido':>7}  {'SNR':>6}")
    for f, lvl, r, snr in medidas:
        barra = '#' * max(0, int(snr / 2))
        marca = ' <- faixa da v5' if tl.fbase <= f <= tl.fbase + 15 * tl.step_hz else (
                ' <- faixa da v4' if 1000 <= f <= 3500 else '')
        print(f"{f:>6}  {lvl:>6.1f}  {r:>6.1f}  {snr:>5.1f}  {barra}{marca}")

    def faixa(lo, hi):
        v = [snr for f, _, _, snr in medidas if lo <= f <= hi]
        return (min(v), float(np.mean(v))) if v else (float('nan'), float('nan'))

    v4, v5 = faixa(1000, 3500), faixa(tl.fbase, tl.fbase + 15 * tl.step_hz)
    print(f"\nSNR por tom  (pior / media):   v4 (1-3,5 kHz): {v4[0]:.0f} / {v4[1]:.0f} dB"
          f"      v5 ({tl.fbase / 1000:g}-{(tl.fbase + 15 * tl.step_hz) / 1000:g} kHz): {v5[0]:.0f} / {v5[1]:.0f} dB")
    if v5[0] < 10:
        print("[ALERTA] A faixa da v5 tem algum tom com SNR abaixo de 10 dB. O hardware ou o ambiente")
        print("         nao sustentam essa faixa; edite fbase/step_hz em transfer_lib.py para onde a SNR for melhor.")
    elif v5[1] > v4[1]:
        print("A faixa da v5 tem SNR melhor que a da v4 neste hardware e neste ambiente.")
    else:
        print("[ATENCAO] Neste hardware a faixa da v4 tem SNR igual ou melhor. Revise a faixa antes de confiar na v5.")


def main():
    ap = argparse.ArgumentParser(description="Mede a SNR por frequencia do seu alto-falante e microfone.")
    ap.add_argument("--arquivo", help="analisa um WAV de calibracao gravado antes")
    ap.add_argument("--salvar", default="calibracao.wav")
    args = ap.parse_args()

    n_silencio = int(SILENCIO_S * tl.Fs)
    if args.arquivo:
        fs, gravacao = wavfile.read(args.arquivo)
        if gravacao.ndim > 1:
            gravacao = gravacao[:, 0]
        if fs != tl.Fs:
            sys.exit(f"O WAV esta em {fs} Hz; esperado {tl.Fs} Hz.")
    else:
        sd = tl._sounddevice()
        sinal, _, _ = sinal_de_teste()
        tocar = tl.normalize_audio(sinal)
        total = np.concatenate((np.zeros(n_silencio, dtype=np.int16), tocar))
        print(f"Medindo por {len(total) / tl.Fs:.0f} s - fique em silencio...")
        gravacao = sd.playrec(total, samplerate=tl.Fs, channels=1, dtype='int16', blocking=True)[:, 0]
        wavfile.write(args.salvar, tl.Fs, gravacao)
        print(f"Gravacao salva em {args.salvar!r}.")

    try:
        medidas, rho = analisar(gravacao, n_silencio)
    except RuntimeError as e:
        sys.exit(f"\n{e}")
    relatorio(medidas)


if __name__ == "__main__":
    main()

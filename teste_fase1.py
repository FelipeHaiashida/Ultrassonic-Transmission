"""
Teste da Fase 1 -- Prova de conceito do protocolo de transmissao por som.

Roda o ciclo completo (texto -> audio -> texto) inteiramente em memoria, sem
microfone nem alto-falante, usando os parametros e tecnicas definidos na
pesquisa desta fase: codificacao por chaveamento de frequencia (FSK),
sincronismo com fade-in, janela de Blackman e deteccao por FFT.

    python teste_fase1.py
"""

import binascii

import numpy as np
from scipy.io import wavfile

# --- Parametros do protocolo (definidos na pesquisa da Fase 1) ---
Fs = 48000       # taxa de amostragem
dur = 0.25       # duracao de cada tom de dado
gap = 0.15       # silencio entre tons
syncDur = 1.5    # duracao do tom de sincronismo
fsync = 19000    # frequencia do sincronismo (quase-ultrassonica)
fbase = 20000    # frequencia do digito hexadecimal 0
step_hz = 100    # distancia entre digitos consecutivos
WARMUP = 2.5     # silencio inicial
MIDGAP = 0.5     # silencio entre o sincronismo e os dados
COOLDOWN = 1.0   # silencio final

AMPLITUDE = 30


def blackman_fade(tom):
    """Suaviza o inicio/fim do tom -- evita estalos audiveis (vazamento espectral)."""
    return tom * np.blackman(len(tom))


def fade_in_sync(sinal):
    """Rampa de entrada lenta no tom de sincronismo, para nao gerar clique."""
    fade_len = int(0.5 * Fs)
    t = np.linspace(-np.pi / 2, 0, fade_len)
    sinal[:fade_len] *= (np.sin(t) + 1)
    fade_out = int(len(sinal) * 0.1)
    sinal[-fade_out:] *= np.linspace(1, 0, fade_out)
    return sinal


def texto_para_sinal(texto):
    """Codifica: texto -> hexadecimal -> tons por chaveamento de frequencia (FSK)."""
    hexstr = binascii.hexlify(texto.encode('utf-8')).decode('ascii')

    t_tom = np.linspace(0, dur, int(dur * Fs), endpoint=False)
    silencio_gap = np.zeros(int(gap * Fs))
    dados = []
    for c in hexstr:
        freq = fbase + step_hz * int(c, 16)
        tom = AMPLITUDE * np.sin(2 * np.pi * freq * t_tom)
        dados.append(blackman_fade(tom))
        dados.append(silencio_gap)
    dados = np.concatenate(dados) if dados else np.zeros(0)

    t_sync = np.linspace(0, syncDur, int(syncDur * Fs), endpoint=False)
    sync = fade_in_sync(AMPLITUDE * np.sin(2 * np.pi * fsync * t_sync))

    sinal = np.concatenate((
        np.zeros(int(WARMUP * Fs)),
        sync,
        np.zeros(int(MIDGAP * Fs)),
        dados,
        np.zeros(int(COOLDOWN * Fs)),
    ))
    return sinal, hexstr


def frequencia_dominante(segmento):
    """FFT do trecho -> frequencia de maior amplitude (o simbolo transmitido)."""
    espectro = np.abs(np.fft.rfft(segmento * np.blackman(len(segmento))))
    return np.argmax(espectro) * Fs / len(segmento)


def sinal_para_texto(sinal):
    """Decodifica: acha o sincronismo, le os tons na sequencia e converte em texto."""
    limiar = 0.1 * np.max(np.abs(sinal))
    acima = np.where(np.abs(sinal) > limiar)[0]
    if len(acima) == 0:
        return ''
    inicio_sync = acima[0]
    inicio_dados = inicio_sync + int(syncDur * Fs) + int(MIDGAP * Fs)

    passo = int((dur + gap) * Fs)
    amostras_tom = int(dur * Fs)
    limiar_tom = 0.05 * np.max(np.abs(sinal))

    digitos = []
    pos = inicio_dados
    while pos + amostras_tom <= len(sinal):
        segmento = sinal[pos: pos + amostras_tom]
        pos += passo
        if np.max(np.abs(segmento)) < limiar_tom:
            continue
        freq = frequencia_dominante(segmento)
        valor = round((freq - fbase) / step_hz)
        if 0 <= valor <= 15:
            digitos.append(format(valor, 'x'))

    hexstr = ''.join(digitos)
    if len(hexstr) % 2:
        hexstr = hexstr[:-1]
    if not hexstr:
        return ''
    return binascii.unhexlify(hexstr).decode('utf-8', errors='ignore')


def main():
    mensagem = "TESTE FASE 1 OK"

    print("=" * 60)
    print("  TESTE DA FASE 1 - Prova de conceito do protocolo")
    print("=" * 60)

    print(f"\n1. Mensagem original: {mensagem!r}")

    sinal, hexstr = texto_para_sinal(mensagem)
    duracao = len(sinal) / Fs
    print(f"2. Codificada em hexadecimal: {hexstr}")
    print(f"3. Sinal de audio gerado: {duracao:.1f}s ({len(sinal)} amostras a {Fs} Hz)")

    audio_int16 = (sinal / np.max(np.abs(sinal)) * 18000).astype(np.int16)
    wavfile.write('fase1_teste.wav', Fs, audio_int16)
    print("4. Audio salvo em 'fase1_teste.wav' (pode ser reproduzido para conferir)")

    recebido = sinal_para_texto(sinal)
    print(f"5. Mensagem decodificada: {recebido!r}")

    print("\n" + "-" * 60)
    if recebido == mensagem:
        print("  RESULTADO: sucesso -- mensagem recuperada corretamente.")
    else:
        print("  RESULTADO: falha -- mensagem NAO recuperada corretamente.")
    print("-" * 60)


if __name__ == '__main__':
    main()

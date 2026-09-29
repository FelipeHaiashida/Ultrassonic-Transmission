import numpy as np
import binascii
from scipy.io import wavfile
import pyaudio

# --- MODO PARANOID (ZERO RUÍDO) ---
dur = 0.25
gap = 0.15
syncDur = 1.5    # Aumentei o tempo de sync para permitir o fade lento
Fs = 48000
fsync = 19000
fbase = 20000
step_hz = 100
# ----------------------------------

def normalize_audio(audio_data):
    m = np.max(np.abs(audio_data))
    if m == 0: return audio_data
    # Volume em 50% (18000) para evitar qualquer distorção harmônica
    normalized = (audio_data / m) * 18000
    return normalized.astype(np.int16)

def fileToHex(filename):
    hexrep = ''
    with open(filename, 'rb') as f:
        content = f.read()
        hexrep += str(binascii.hexlify(content))
    return hexrep[2:-1]

def apply_blackman_fade(signal):
    # Janela Blackman padrão para as notas de dados
    window = np.blackman(len(signal))
    return signal * window

def apply_super_slow_fade(signal, Fs):
    # FADE ESPECÍFICO PARA O INÍCIO (SYNC)
    # Cria uma rampa de entrada de 0.5 segundos (muito lenta)
    fade_len = int(0.5 * Fs)

    if fade_len > len(signal): fade_len = len(signal)

    # Curva suave de 0 a 1 usando metade de uma senoide (Hanning)
    # Isso é mais suave que uma linha reta
    t = np.linspace(-np.pi/2, 0, fade_len)
    fade_curve = np.sin(t) + 1 # Vai de 0 a 1 suavemente

    # Aplica no começo
    signal[:fade_len] *= fade_curve

    # O final pode ser Blackman normal (fade out rápido)
    fade_out_len = int(len(signal) * 0.1)
    fade_out = np.linspace(1, 0, fade_out_len)
    signal[-fade_out_len:] *= fade_out

    return signal

def hexToAudio_Wrapper(hexstr):
    # 1. Gera os DADOS primeiro
    t_tone = np.linspace(0, dur, int(dur*Fs), endpoint=False)
    silence_gap = np.zeros(int(gap*Fs))
    data_audio = np.zeros(0)

    for char in hexstr:
        val = int(char, 16)
        f = fbase + (step_hz * val)
        tone = 30 * np.sin(2 * np.pi * f * t_tone)
        tone = apply_blackman_fade(tone)
        data_audio = np.concatenate((data_audio, tone, silence_gap))

    # 2. Gera o SYNC com Fade Lento
    t_sync = np.linspace(0, syncDur, int(syncDur*Fs))
    syncTone = 30 * np.sin(2 * np.pi * fsync * t_sync)
    syncTone = apply_super_slow_fade(syncTone, Fs)

    # 3. Monta o sanduíche final
    warmup = np.zeros(int(Fs * 2.5)) # 2.5s de silêncio inicial
    mid_silence = np.zeros(int(Fs * 0.5))
    cooldown = np.zeros(int(Fs * 1.0))

    final = np.concatenate((warmup, syncTone, mid_silence, data_audio, cooldown))
    return final

def writeFileToWav(filename, outputFile='output.wav'):
    try:
        hexstr = fileToHex(filename)
        print(f"Hex: {hexstr}")
        print("Gerando áudio com 'Entrada de Veludo'...")

        # Usa a nova função wrapper que organiza melhor
        signal = hexToAudio_Wrapper(hexstr)

        signal_norm = normalize_audio(signal)
        wavfile.write(outputFile, Fs, signal_norm)
        return True
    except Exception as e:
        print(f"Erro: {e}")
        return False

def record(Fs=Fs, fsync=fsync, CHUNK=1024):
    p = pyaudio.PyAudio()
    theaudio = np.zeros(0)

    try:
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=Fs, input=True, frames_per_buffer=CHUNK)
    except OSError as e:
        print("Erro Audio.")
        raise e

    print(f"Ouvindo... (Aguardando silêncio longo inicial)")
    recording = False
    silence_count = 0

    while True:
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            numpydata = np.frombuffer(data, dtype=np.int16)

            ft = np.fft.fft(numpydata)
            ft = np.abs(ft[:len(ft)//2])
            ft[:20] = 0

            if len(ft) == 0: continue

            freqidx = int(np.argmax(ft) * (Fs / (len(ft)*2)))

            if not recording:
                if fsync - 300 < freqidx < fsync + 300:
                    print(f"--> Sincronia detectada ({freqidx}Hz)")
                    recording = True
                    theaudio = np.concatenate((theaudio, np.zeros(Fs)))

            if recording:
                theaudio = np.concatenate((theaudio, numpydata))
                if len(theaudio) > 30 * Fs: break

                if np.max(np.abs(numpydata)) < 150:
                    silence_count += 1
                else:
                    silence_count = 0

                if silence_count > (Fs/CHUNK) * 2.5:
                    print("Fim.")
                    break
        except KeyboardInterrupt:
            break

    stream.close()
    p.terminate()
    return theaudio

def get_dominant_freq(segment):
    w = np.blackman(len(segment))
    ft = np.fft.fft(segment * w)
    ft = np.abs(ft[:len(ft)//2])
    idx = np.argmax(ft)
    return idx * Fs / len(segment)

def writeSignalToFile(signal, outputFile='decoded_out'):
    print("\n--- DECODIFICANDO ---")

    samples_tone = int(dur * Fs)
    samples_gap = int(gap * Fs)
    hex_chars = []

    # Busca dinâmica: Ignora o warmup gigante e acha onde começa o dado
    # Sabemos que depois do Sync tem 0.5s de silêncio e depois começam os dados

    # Procura onde o Sync termina (quando o volume cai)
    # Mas como já temos o buffer gravado a partir do sync, podemos pular fixo:
    # O record() começa a gravar quando ouve o Sync.
    # O Sync dura 1.5s. Depois tem 0.5s de silêncio.
    # Offset = 2.0s

    start_offset = int(2.0 * Fs)
    curr_pos = start_offset

    while curr_pos + samples_tone < len(signal):
        segment = signal[curr_pos : curr_pos + samples_tone]
        curr_pos += (samples_tone + samples_gap)

        if np.max(np.abs(segment)) < 150:
             continue

        freq = get_dominant_freq(segment)

        if 19500 < freq < 22000:
            val_f = (freq - fbase) / step_hz
            val = int(round(val_f))
            if 0 <= val <= 15:
                print(f"{format(val, 'x')}", end="")
                hex_chars.append(format(val, 'x'))

    raw_hex = "".join(hex_chars)
    print(f"\nHex: {raw_hex}")

    if len(raw_hex) % 2 != 0: raw_hex = raw_hex[:-1]

    try:
        if raw_hex:
            decoded_bytes = binascii.unhexlify(raw_hex)
            decoded_text = decoded_bytes.decode('utf-8', errors='ignore')
            with open(outputFile, 'w') as f:
                f.write(decoded_text)
            print(f"\nTEXTO: {decoded_text}")
        else:
            print("\nNada.")
    except Exception as e:
        print(f"Erro: {e}")

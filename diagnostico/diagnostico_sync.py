"""
diagnostico_sync.py - testa a transmissao por som entre 2 PCs e explica POR QUE
deu certo ou errado.

O jogo so diz "resposta ininteligivel". Esta ferramenta separa o problema em
etapas e mede cada uma:

    1. O microfone ouviu alguma coisa?          (nivel, clipping, overflow)
    2. O canal acustico entrega os tons?         (SNR e frequencia de cada tom,
                                                  medidos com alinhamento ideal)
    3. O detector de sync do transfer_lib dispara na hora certa?
                                                 (ou dispara com ruido?)
    4. O receptor detecta o fim da transmissao? (ou so para no teto de 30 s?)
    5. find_data_start alinha os tons certo?    (erro em ms)
    6. O resultado final bate com o esperado?

Se (2) esta bom e (6) falha, o problema e de software (etapas 3-5), nao do
som. Se (2) ja falha, o problema e de hardware/ambiente.

Uso - PC A emite, PC B recebe:

    python diagnostico_sync.py info                    # dispositivos de audio
    python diagnostico_sync.py receber --duracao 60    # no PC B, comece ANTES
    python diagnostico_sync.py emitir --repeticoes 3   # no PC A

    python diagnostico_sync.py analisar gravacao.wav   # reanalisa uma gravacao
    python diagnostico_sync.py simular --ruido-rms 60  # sem hardware nenhum

A mensagem de teste e o hex "0123456789abcdef": passa por todos os 16 tons,
entao da para ver se alguma frequencia especifica se perde no caminho.
"""

import argparse
import datetime
import os
import sys
import time

import numpy as np
from scipy.io import wavfile
from scipy.signal import fftconvolve, resample_poly

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)

HEX_TESTE = "0123456789abcdef"
LIMIAR_NCC = 0.15       # correlacao minima para considerar que achou a transmissao
# Erro de alinhamento que o decodificador aguenta. Assimetrico: a leitura cobre
# 50-200 ms de cada tom de 250 ms. Adiantada, ela entra no silencio/eco do tom
# ANTERIOR (frequencia errada); atrasada, le a cauda e o eco do MESMO tom, e o
# proximo so comeca em 400 ms. Com eco na sala o alinhamento tende a atrasar.
TOLERANCIA_ANTES_MS = 50
TOLERANCIA_DEPOIS_MS = 100


def dentro_da_margem(erro_ms):
    return -TOLERANCIA_ANTES_MS <= erro_ms <= TOLERANCIA_DEPOIS_MS


MARGEM = f"-{TOLERANCIA_ANTES_MS}/+{TOLERANCIA_DEPOIS_MS} ms"
SNR_MINIMO_DB = 15      # abaixo disso o tom esta enterrado no ruido

tl = None   # transfer_lib da versao escolhida, carregado em main()


def carregar_lib(versao, perfil):
    global tl
    pasta = os.path.join(RAIZ, versao)
    if not os.path.isfile(os.path.join(pasta, "transfer_lib.py")):
        raise SystemExit(f"Nao achei {versao}/transfer_lib.py em {RAIZ}")
    sys.path.insert(0, pasta)
    import transfer_lib
    tl = transfer_lib
    tl.usar_perfil(perfil)


class Relatorio:
    """print() que tambem guarda o texto, para salvar junto da gravacao."""

    def __init__(self):
        self.linhas = []

    def __call__(self, texto=""):
        print(texto)
        self.linhas.append(texto)

    def salvar(self, caminho):
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("\n".join(self.linhas) + "\n")


def agora():
    return datetime.datetime.now().strftime("%H:%M:%S")


# ------------------------------------------------------------------- sinal

def passo():
    return int((tl.dur + tl.gap) * tl.Fs)


def gerar_teste(hexstr):
    """Sinal completo (int16, como sai no alto-falante) e o trecho so de dados,
    que serve de molde para achar a transmissao dentro da gravacao."""
    sinal = tl.normalize_audio(tl.hex_to_signal(hexstr))
    ini = int(tl.WARMUP * tl.Fs) + int(tl.syncDur * tl.Fs) + int(tl.MIDGAP * tl.Fs)
    molde = sinal[ini:ini + len(hexstr) * passo()].astype(np.float64)
    return sinal, molde


def freq_do_digito(c):
    return tl.fbase + tl.step_hz * int(c, 16)


def freq_na_faixa(seg):
    """Como dominant_freq, mas so procura o pico entre o digito 0 e o f."""
    ft = np.abs(np.fft.rfft(seg * np.blackman(len(seg))))
    hz = np.arange(len(ft)) * tl.Fs / len(seg)
    faixa = (hz >= tl.fbase - tl.step_hz) & (hz <= tl.fbase + 16 * tl.step_hz)
    return hz[faixa][np.argmax(ft[faixa])]


def amplitude_em(seg, f):
    """Amplitude do componente em f (maior valor em +-2 bins)."""
    ft = np.abs(np.fft.rfft(seg * np.blackman(len(seg))))
    k = int(round(f * len(seg) / tl.Fs))
    return ft[max(0, k - 2):k + 3].max() if len(ft) else 0.0


# ------------------------------------------------------------ comandos

def cmd_info(args):
    sd = tl._sounddevice()
    print(f"Versao do protocolo: {args.versao}, perfil {args.perfil}, Fs = {tl.Fs} Hz\n")
    print(sd.query_devices())
    print(f"\nPadrao (entrada, saida): {sd.default.device}")
    for nome, checar, disp in (("entrada", sd.check_input_settings, args.entrada),
                               ("saida", sd.check_output_settings, args.saida)):
        try:
            checar(device=disp, channels=1, dtype="int16", samplerate=tl.Fs)
            print(f"[OK]    {nome}: aceita mono int16 a {tl.Fs} Hz")
        except Exception as e:
            print(f"[FALHA] {nome}: nao aceita mono int16 a {tl.Fs} Hz -> {e}")
    print("\nDica (Windows): em Configuracoes > Som > dispositivo > Propriedades,")
    print("desligue 'Aprimoramentos de audio' / 'Supressao de ruido' / 'Cancelamento")
    print("de eco' do microfone. Esses filtros tratam tons longos como ruido e apagam.")


def cmd_emitir(args):
    sd = tl._sounddevice()
    sinal, _ = gerar_teste(args.hex)
    dur_s = len(sinal) / tl.Fs
    print(f"Versao {args.versao}, perfil {args.perfil}. Mensagem de teste (hex): {args.hex}")
    print(f"Sync em {tl.fsync} Hz por {tl.syncDur}s; dados de {tl.fbase} a "
          f"{tl.fbase + 15 * tl.step_hz} Hz; {dur_s:.1f}s por transmissao.")
    print("O receptor tem que estar rodando 'receber' ANTES. Comecando em 3 s...")
    time.sleep(3)
    for r in range(args.repeticoes):
        print(f"[{agora()}] tocando transmissao {r + 1}/{args.repeticoes} ({dur_s:.1f}s)")
        sd.play(sinal, samplerate=tl.Fs, blocking=True, device=args.saida)
        if r < args.repeticoes - 1:
            time.sleep(args.intervalo)
    print(f"[{agora()}] fim. Pode parar o receptor (Ctrl+C) - ele analisa sozinho.")


def cmd_receber(args):
    sd = tl._sounddevice()
    stream = sd.InputStream(samplerate=tl.Fs, channels=1, dtype="int16",
                            blocksize=tl.CHUNK, device=args.entrada)
    blocos, overflows = [], 0
    total = int(args.duracao * tl.Fs / tl.CHUNK)
    print(f"Gravando ate {args.duracao}s (Ctrl+C para parar antes). Pode emitir agora.")
    print("  pico = amplitude maxima (int16), freq = frequencia dominante do bloco\n")
    stream.start()
    ultimo = 0.0
    try:
        for i in range(total):
            dados, overflow = stream.read(tl.CHUNK)
            overflows += bool(overflow)
            bloco = dados[:, 0].copy()
            blocos.append(bloco)
            if time.time() - ultimo > 0.5:
                ultimo = time.time()
                ft = np.abs(np.fft.rfft(bloco))
                ft[:5] = 0
                hz = np.argmax(ft) * tl.Fs / tl.CHUNK
                pico = int(np.abs(bloco.astype(np.int32)).max())
                barra = "#" * min(40, int(np.log10(max(pico, 1)) * 8))
                print(f"\r  {i * tl.CHUNK / tl.Fs:5.1f}s  pico {pico:6d}  freq {hz:6.0f} Hz  {barra:<40}",
                      end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()
    print()

    audio = np.concatenate(blocos) if blocos else np.zeros(0, dtype=np.int16)
    base = datetime.datetime.now().strftime("gravacao_%Y%m%d_%H%M%S")
    wavfile.write(base + ".wav", tl.Fs, audio)
    print(f"Gravacao salva em {base}.wav ({len(audio) / tl.Fs:.1f}s)\n")
    rel = Relatorio()
    analisar(audio, args.hex, rel, overflows=overflows)
    rel.salvar(base + "_relatorio.txt")
    print(f"\nRelatorio salvo em {base}_relatorio.txt")


def cmd_analisar(args):
    fs, audio = wavfile.read(args.arquivo)
    if audio.ndim > 1:
        audio = audio[:, 0]
    if audio.dtype.kind == "f":
        audio = np.clip(audio * 32767, -32768, 32767)
    elif audio.dtype == np.int32:
        audio = audio / 65536
    if fs != tl.Fs:
        print(f"(reamostrando {fs} -> {tl.Fs} Hz)")
        audio = resample_poly(audio.astype(np.float64), tl.Fs, fs)
    audio = np.asarray(audio).astype(np.int16)
    rel = Relatorio()
    analisar(audio, args.hex, rel)
    rel.salvar(os.path.splitext(args.arquivo)[0] + "_relatorio.txt")


def cmd_simular(args):
    """Monta uma 'gravacao' sintetica: ruido de sala + N transmissoes, com o
    volume e o eco pedidos. Serve para validar a ferramenta sem hardware e
    para ver como cada fator sozinho afeta o receptor."""
    rng = np.random.default_rng(args.seed)
    Fs = tl.Fs
    sinal, _ = gerar_teste(args.hex)
    sinal = sinal.astype(np.float64) / np.abs(sinal).max() * args.nivel
    if args.reverb > 0:
        n = int(args.reverb * Fs)
        ir = rng.normal(0, 1, n) * np.exp(-6.9 * np.arange(n) / n)   # -60 dB no fim
        ir[0] = 0
        ir *= 0.5 / np.sqrt(np.sum(ir ** 2))
        ir[0] = 1.0
        sinal = fftconvolve(sinal, ir)[:len(sinal) + n]

    partes = [np.zeros(int(4 * Fs))]
    for r in range(args.repeticoes):
        partes.append(sinal)
        partes.append(np.zeros(int(args.intervalo * Fs)))
    partes.append(np.zeros(int(4 * Fs)))
    audio = np.concatenate(partes)

    if args.ruido_rms > 0:
        # Ruido rosa (1/f): mais parecido com sala/ventoinha que ruido branco.
        w = np.fft.rfft(rng.normal(0, 1, len(audio)))
        fr = np.fft.rfftfreq(len(audio), 1 / Fs)
        w[1:] /= np.sqrt(fr[1:])
        w[0] = 0
        ruido = np.fft.irfft(w, len(audio))
        audio += ruido / ruido.std() * args.ruido_rms

    audio = np.clip(audio, -32768, 32767).astype(np.int16)
    wavfile.write("simulacao.wav", Fs, audio)
    print(f"Simulacao: nivel {args.nivel}, ruido rosa rms {args.ruido_rms}, "
          f"reverb {args.reverb}s, {args.repeticoes} transmissoes -> simulacao.wav\n")
    rel = Relatorio()
    analisar(audio, args.hex, rel)
    rel.salvar("simulacao_relatorio.txt")


# ------------------------------------------------------------- analise

def localizar_transmissoes(x, molde):
    """Acha cada transmissao por correlacao com o trecho de dados conhecido.

    E o receptor 'ideal': nao depende do detector de sync nem de limiares de
    amplitude. Se ele acha os tons e o transfer_lib nao, o problema e do
    software, nao do som.
    """
    if len(x) < len(molde):
        return []
    corr = fftconvolve(x, molde[::-1], mode="valid")
    energia = fftconvolve(x ** 2, np.ones(len(molde)), mode="valid")
    ncc = np.abs(corr) / (np.linalg.norm(molde) * np.sqrt(np.maximum(energia, 1e-9)))
    achadas = []
    while len(achadas) < 50:
        i = int(np.argmax(ncc))
        if ncc[i] < LIMIAR_NCC:
            break
        achadas.append((i, float(ncc[i])))
        ncc[max(0, i - len(molde)):i + len(molde)] = 0
    return sorted(achadas)


def lib_nova():
    """A v4 consertada tem receptor por proeminencia (analisar_bloco); a v3
    ainda usa o detector antigo, que esta ferramenta replica."""
    return hasattr(tl, "analisar_bloco")


def simular_receptor(audio):
    """Roda o receptor sobre a gravacao inteira, em sequencia, como um
    receptor que volta a escutar assim que termina uma recepcao. Anota ONDE
    cada recepcao comecou, onde parou e POR QUE parou."""
    if lib_nova():
        sessoes, pos = [], 0
        while pos < len(audio) - tl.CHUNK:
            info = {}
            trecho = tl._coletar(tl._blocos_do_array(audio[pos:]), verbose=False, info=info)
            if info["ini"] is None:
                break
            sessoes.append(dict(ini=pos + info["ini"], fim=pos + info["fim"],
                                motivo=info["motivo"], hex=tl.signal_to_hex(trecho),
                                data_start=tl.find_data_start(trecho.astype(np.float64))))
            pos += info["fim"]
        return sessoes
    return _simular_receptor_antigo(audio)


def _simular_receptor_antigo(audio):
    """Replica exata do _coletar antigo (v3 e v4 antes do conserto)."""
    CH = tl.CHUNK
    nb = len(audio) // CH
    limite_silencio = (tl.Fs / CH) * tl.FIM_SILENCIO
    sessoes, i = [], 0
    while i < nb:
        ini = None
        silencio = total = 0
        motivo = "acabou a gravacao"
        j = i
        while j < nb:
            bloco = audio[j * CH:(j + 1) * CH]
            if ini is None and dispara_sync(bloco):
                ini = j
            if ini is not None:
                total += CH
                if total > tl.MAX_RECORD_S * tl.Fs:
                    motivo = f"teto de {tl.MAX_RECORD_S}s"
                    break
                silencio = silencio + 1 if np.max(np.abs(bloco)) < tl.SILENCE_FLOOR else 0
                if silencio > limite_silencio:
                    motivo = "silencio final"
                    break
            j += 1
        if ini is None:
            break
        fim = min(j + 1, nb)
        trecho = audio[ini * CH:fim * CH]
        sessoes.append(dict(ini=ini * CH, fim=fim * CH, motivo=motivo,
                            hex=tl.signal_to_hex(trecho),
                            data_start=tl.find_data_start(trecho.astype(np.float64))))
        i = fim
    return sessoes


def dispara_sync(bloco):
    """Um bloco sozinho seria tomado por sync?"""
    if lib_nova():
        return tl.analisar_bloco(bloco)[1]
    ft = np.abs(np.fft.rfft(bloco))
    ft[:20] = 0
    hz = np.argmax(ft) * tl.Fs / len(bloco)
    return tl.fsync - 300 < hz < tl.fsync + 300


def leitura_da_lib(seg):
    """Frequencia que o decodificador do transfer_lib leria neste trecho."""
    if hasattr(tl, "_tom_na_faixa"):
        return tl._tom_na_faixa(seg)[0]
    return tl.dominant_freq(seg)


def comparar(esperado, lido):
    marcas = "".join("^" if i >= len(lido) or lido[i] != c else " "
                     for i, c in enumerate(esperado))
    return marcas + ("  (+%d a mais)" % (len(lido) - len(esperado)) if len(lido) > len(esperado) else "")


def analisar(audio, hexstr, rel, overflows=None):
    Fs, CH = tl.Fs, tl.CHUNK
    x = audio.astype(np.float64)
    _, molde = gerar_teste(hexstr)
    p = passo()
    recuo, largura = int(0.20 * tl.dur * Fs), int(0.60 * tl.dur * Fs)
    pre_dados = int(tl.syncDur * Fs) + int(tl.MIDGAP * Fs)   # sync + midgap antes dos dados
    problemas = []

    rel("=" * 70)
    rel(f"  DIAGNOSTICO - {len(audio) / Fs:.1f}s de audio, perfil com sync {tl.syncDur}s "
        f"@ {tl.fsync} Hz, FIM_SILENCIO {tl.FIM_SILENCIO}s")
    rel(f"  Mensagem esperada (hex): {hexstr}")
    rel("=" * 70)

    if len(audio) < CH * 10:
        rel("[FALHA] Gravacao curta demais para analisar.")
        return

    # --- onde estao as transmissoes de verdade
    trans = localizar_transmissoes(x, molde)
    ocupado = np.zeros(len(audio), dtype=bool)
    for ini, _ in trans:
        ocupado[max(0, ini - pre_dados - int(0.3 * Fs)):ini + len(molde) + int(0.8 * Fs)] = True

    nb = len(audio) // CH
    blocos = audio[:nb * CH].reshape(nb, CH)
    picos = np.abs(blocos.astype(np.int32)).max(axis=1)
    so_ruido = ~ocupado[:nb * CH].reshape(nb, CH).any(axis=1)

    # ------------------------------------------------ 1. hardware / nivel
    rel("\n1) NIVEL DO MICROFONE")
    if overflows:
        rel(f"   [FALHA] {overflows} overflow(s) de entrada: amostras foram perdidas,")
        rel("           o que desalinha os tons. Feche outros programas de audio.")
        problemas.append("overflow de entrada")
    elif overflows == 0:
        rel("   [OK]    nenhum overflow de entrada")

    ruido_p50 = int(np.median(picos[so_ruido])) if so_ruido.any() else None
    ruido_p90 = int(np.percentile(picos[so_ruido], 90)) if so_ruido.any() else None
    sinal_pico = int(picos[~so_ruido].max()) if (~so_ruido).any() else 0
    clip = int(np.sum(np.abs(audio.astype(np.int32)) >= 32700))
    if ruido_p50 is not None:
        rel(f"   ruido de fundo: pico tipico {ruido_p50}, pico p90 {ruido_p90}"
            + ("" if lib_nova() else f" (SILENCE_FLOOR do receptor = {tl.SILENCE_FLOOR})"))
    if trans:
        rel(f"   pico durante as transmissoes: {sinal_pico}")
    if clip:
        rel(f"   [ALERTA] {clip} amostras saturadas (clipping) - baixe o volume ou o ganho do mic")
        problemas.append("clipping")

    # ------------------------------------------------ 2. canal acustico
    rel(f"\n2) CANAL ACUSTICO (receptor ideal, alinhado por correlacao)")
    if not trans:
        rel("   [FALHA] Nenhuma transmissao encontrada na gravacao.")
        rel("           O microfone nao captou os tons: volume do emissor, dispositivo de")
        rel("           entrada errado, mic mudo, ou 'Supressao de ruido' do Windows apagando.")
        problemas.append("transmissao nao captada")
    ref_ruido = None
    if so_ruido.any():
        # trecho mais silencioso da gravacao, como referencia de ruido
        idx = np.flatnonzero(so_ruido)
        k = idx[np.argmin(picos[idx])]
        ref_ruido = x[k * CH:k * CH + largura]
        if len(ref_ruido) < largura:
            ref_ruido = None

    def snr(seg, f):
        if ref_ruido is None:
            return float("inf")
        return 20 * np.log10(max(amplitude_em(seg, f), 1e-9) / max(amplitude_em(ref_ruido, f), 1e-9))

    canal_ok = []
    for n, (ini, ncc) in enumerate(trans, 1):
        rel(f"\n   Transmissao #{n} em {ini / Fs:.2f}s (dados)  correlacao {ncc:.2f}")
        s0 = ini - pre_dados
        seg_sync = x[max(0, s0) + int(0.33 * tl.syncDur * Fs):ini - int(tl.MIDGAP * Fs)]
        if len(seg_sync) > 256:
            rel(f"   sync {tl.fsync} Hz: SNR {min(snr(seg_sync, tl.fsync), 99):5.1f} dB")
        # O receptor ideal le o pico so DENTRO da faixa dos dados. Em paralelo,
        # anota o que o transfer_lib leria (pico do espectro inteiro): se os dois
        # divergem, o tom chegou mas um ruido fora da faixa o encobre.
        lidos, lib_lidos, fracos, mascarados = "", "", [], []
        for k, c in enumerate(hexstr):
            a = ini + k * p + recuo
            seg = x[a:a + largura]
            if len(seg) < largura:
                break
            f_esp = freq_do_digito(c)
            f_banda = freq_na_faixa(seg)
            v = int(round((f_banda - tl.fbase) / tl.step_hz))
            d = format(v, "x") if 0 <= v <= 15 else "?"
            lidos += d
            f_lib = leitura_da_lib(seg)
            v = int(round((f_lib - tl.fbase) / tl.step_hz))
            d_lib = format(v, "x") if 0 <= v <= 15 and tl.fbase - 500 < f_lib < tl.fbase + 16 * tl.step_hz + 500 else "?"
            lib_lidos += d_lib
            s = snr(seg, f_esp)
            if d != c or s < SNR_MINIMO_DB:
                fracos.append(f"{c}({f_esp}Hz): SNR {s:.0f}dB, pico na faixa em {f_banda:.0f}Hz -> '{d}'")
            elif d_lib != c:
                mascarados.append(f"{c}({f_esp}Hz): SNR {s:.0f}dB, mas o pico do espectro inteiro esta em {f_lib:.0f}Hz")
        rel(f"   tons lidos: {lidos}")
        rel(f"               {comparar(hexstr, lidos)}")
        for fr in fracos:
            rel(f"     - {fr}")
        ok = lidos == hexstr
        canal_ok.append(ok)
        rel(f"   {'[OK]    canal entrega os 16 tons certos' if ok else '[FALHA] o proprio som chega errado - problema de hardware/ambiente'}")
        if mascarados:
            rel(f"   [FALHA] mas o decodificador do transfer_lib leria: {lib_lidos}")
            rel(f"                                                     {comparar(hexstr, lib_lidos)}")
            for m in mascarados:
                rel(f"     - {m}")
            rel("           Ele pega o maior pico do espectro INTEIRO, sem filtrar a faixa dos")
            rel("           dados: um ruido fora da faixa (grave, vibracao) mais forte que o tom")
            rel("           vence. O som chegou certo - falta um filtro passa-faixa no software.")
            problemas.append("ruido fora da faixa encobre os tons (falta filtro no decodificador)")

    # ------------------------------------------------ 3. detector de sync
    if lib_nova():
        n_sync = max(1, int(round(min(tl.SYNC_MIN_S, 0.4 * tl.syncDur) * Fs / CH)))
        rel(f"\n3) DETECTOR DE SYNC do transfer_lib (tom em {tl.fsync} Hz destacado "
            f"{tl.PROEMINENCIA:g}x dos vizinhos, por {n_sync} blocos seguidos)")
    else:
        rel(f"\n3) DETECTOR DE SYNC do transfer_lib (FFT por bloco, pico entre "
            f"{tl.fsync - 300} e {tl.fsync + 300} Hz)")
    if so_ruido.any():
        marcas = np.array([dispara_sync(b) for b in blocos[so_ruido]])
        falsos, tot = int(marcas.sum()), int(so_ruido.sum())
        pct = 100 * falsos / tot
        if lib_nova():
            seguidos = maior = 0
            for m in marcas:
                seguidos = seguidos + 1 if m else 0
                maior = max(maior, seguidos)
            rel(f"   em trechos SEM transmissao, {falsos}/{tot} blocos ({pct:.0f}%) parecem sync;")
            rel(f"   a maior sequencia seguida foi de {maior} (o receptor exige {n_sync})")
            if maior >= n_sync:
                rel("   [FALHA] o ruido sustentou um falso sync - ha um tom perto de "
                    f"{tl.fsync} Hz na sala?")
                problemas.append("ruido sustentado na frequencia do sync")
            else:
                rel("   [OK]    nenhum trecho de ruido dura o bastante para disparar")
        else:
            rel(f"   em trechos SEM transmissao, {falsos}/{tot} blocos ({pct:.0f}%) disparariam o sync")
            if pct > 1:
                rel("   [FALHA] o detector confunde ruido ambiente com sync. Ele zera so ate ~940 Hz")
                rel("           e aceita o maior pico do bloco, sem exigir que ele se destaque do")
                rel("           ruido nem que dure: ruido de sala (mais forte nos graves) tem o pico")
                rel("           logo acima do corte, dentro da janela do sync. A gravacao comeca na")
                rel("           hora errada.")
                problemas.append(f"sync dispara com ruido ({pct:.0f}% dos blocos)")
            else:
                rel("   [OK]    ruido ambiente nao dispara o sync")

    # ------------------------------------------------ 4. fim da transmissao
    if lib_nova():
        rel(f"\n4) DETECCAO DE FIM (precisa de {tl.FIM_SILENCIO}s seguidos sem tom do protocolo)")
        if so_ruido.any():
            com_tom = 100 * np.mean([tl.analisar_bloco(b)[0] for b in blocos[so_ruido]])
            rel(f"   em trechos SEM transmissao, {com_tom:.0f}% dos blocos tem algo parecido com tom")
            if com_tom > 20:
                rel("   [ALERTA] isso atrasa o fim da recepcao (som tonal na sala: voz, musica, apito?)")
                problemas.append("som tonal na sala atrasa o fim da recepcao")
            else:
                rel("   [OK]    o ruido de fundo nao impede o fim da recepcao")
    else:
        rel(f"\n4) DETECCAO DE FIM (precisa de {tl.FIM_SILENCIO}s com todo bloco abaixo de "
            f"{tl.SILENCE_FLOOR})")
        if ruido_p50 is not None:
            if ruido_p50 >= tl.SILENCE_FLOOR:
                rel(f"   [FALHA] o ruido de fundo (pico {ruido_p50}) ja passa do SILENCE_FLOOR: o")
                rel(f"           receptor nunca ve silencio e so para no teto de {tl.MAX_RECORD_S}s.")
                rel("           Se isso acontecer depois de um disparo falso, a transmissao seguinte")
                rel("           chega enquanto ele ainda esta preso na gravacao anterior.")
                problemas.append("ruido acima do SILENCE_FLOOR - fim nunca detectado")
            elif ruido_p90 >= tl.SILENCE_FLOOR:
                rel(f"   [ALERTA] ruido as vezes passa do limiar (p90 = {ruido_p90}): o fim pode demorar")
                problemas.append("ruido perto do SILENCE_FLOOR")
            else:
                rel("   [OK]    ruido de fundo abaixo do limiar de silencio")

    # ------------------------------------------------ 5/6. receptor de verdade
    rel("\n5) RECEPTOR DO transfer_lib RODANDO SOBRE ESTA GRAVACAO")
    sessoes = simular_receptor(audio)
    if not sessoes:
        rel("   [FALHA] o detector de sync nunca disparou - nada seria gravado.")
        problemas.append("sync nunca detectado")
    gravadas = set()
    acertos = falsas = 0
    for n, s in enumerate(sessoes, 1):
        rel(f"\n   Recepcao #{n}: gravou de {s['ini'] / Fs:.2f}s a {s['fim'] / Fs:.2f}s, "
            f"parou por: {s['motivo']}")
        dentro = [t for t, (ini, _) in enumerate(trans)
                  if s["ini"] <= ini and s["fim"] >= ini + len(molde)]
        cortadas = [t for t, (ini, _) in enumerate(trans)
                    if t not in dentro and s["ini"] < ini + len(molde) and s["fim"] > ini]
        for t in cortadas:
            rel(f"   [FALHA] pegou so um pedaco da transmissao #{t + 1} (gravacao comecou ou parou no meio)")
        if not dentro:
            if not cortadas:
                falsas += 1
                rel("   [FALHA] gravacao disparada sem transmissao nenhuma dentro (disparo falso)")
            if s["hex"]:
                rel(f"           decodificou lixo: {s['hex']!r}")
            continue
        if len(dentro) > 1:
            rel(f"   [FALHA] uma unica recepcao engoliu {len(dentro)} transmissoes "
                f"(#{', #'.join(str(t + 1) for t in dentro)}) - no jogo, a 2a jogada se perderia")
        gravadas.update(dentro)
        ini = trans[dentro[0]][0]
        adiant = (ini - pre_dados - s["ini"]) / Fs
        if adiant > 0.2:
            rel(f"   [ALERTA] comecou a gravar {adiant:.2f}s ANTES do sync real (disparo por ruido)")
        elif adiant < -tl.syncDur:
            rel(f"   [FALHA] comecou a gravar {-adiant:.2f}s DEPOIS do inicio do sync")
        else:
            rel(f"   [OK]    sync detectado {-adiant * 1000:+.0f} ms em relacao ao inicio real")
        if s["data_start"] is None:
            rel("   [FALHA] find_data_start nao achou o inicio dos dados")
        else:
            erro = (s["ini"] + s["data_start"] - ini) / Fs * 1000
            # Os tons sao lidos numa grade de passo fixo: errar por um passo
            # inteiro so pula/inventa digitos, o que importa e o resto.
            passo_ms = p / Fs * 1000
            efetivo = (erro + passo_ms / 2) % passo_ms - passo_ms / 2
            if dentro_da_margem(erro):
                rel(f"   [OK]    find_data_start alinhou com erro de {erro:+.0f} ms")
            else:
                rel(f"   [FALHA] find_data_start errou por {erro:+.0f} ms")
                if abs(erro / 1000 + tl.syncDur + tl.MIDGAP) < 0.3:
                    rel("           Ele devolveu o inicio do SYNC, nao dos dados: um pico de ruido antes")
                    rel("           do sync foi tomado como 'sync', e o sync real como 'primeiro tom'.")
                if dentro_da_margem(efetivo):
                    rel(f"           A grade de leitura (passo {passo_ms:.0f} ms) ainda cai nos tons por")
                    rel(f"           coincidencia ({efetivo:+.0f} ms) - com outro perfil ou mais eco, erra.")
                else:
                    rel(f"           A grade de leitura fica {efetivo:+.0f} ms fora do centro dos tons")
                    rel(f"           (margem segura {MARGEM}): acerta ou erra conforme o eco.")
                problemas.append("find_data_start desalinhado")
        esperado = hexstr * len(dentro)
        rel(f"   esperado: {esperado}")
        rel(f"   recebido: {s['hex']}")
        rel(f"             {comparar(esperado, s['hex'])}")
        if s["hex"] == esperado:
            acertos += len(dentro)
            rel("   [OK]    MENSAGEM CORRETA")
        else:
            culpa = ("o som ja chegou errado (ver etapa 2)" if not all(canal_ok[t] for t in dentro)
                     else "o som chegou certo - o erro e do receptor (etapas 3-5)")
            rel(f"   [FALHA] MENSAGEM ERRADA - {culpa}")
            problemas.append("mensagem decodificada errada")
    for t, (ini, _) in enumerate(trans):
        if t not in gravadas:
            rel(f"\n   [FALHA] Transmissao #{t + 1} ({ini / Fs:.2f}s) nao foi recebida inteira: o")
            rel("           receptor estava ocupado com outra recepcao ou nao detectou o sync.")
            problemas.append(f"transmissao #{t + 1} perdida")
    if falsas:
        rel(f"\n   [FALHA] {falsas} recepcao(oes) falsa(s). No jogo, receber_texto() devolve a")
        rel("           PRIMEIRA recepcao: um disparo falso antes da jogada vira 'resposta")
        rel("           ininteligivel' e a jogada de verdade toca para ninguem.")
        problemas.append(f"{falsas} recepcao(oes) falsa(s) - no jogo, jogada perdida")

    # ------------------------------------------------ veredito
    rel("\n" + "=" * 70)
    rel(f"  RESULTADO: {acertos}/{len(trans)} transmissoes decodificadas certo pelo transfer_lib")
    rel(f"             {sum(canal_ok)}/{len(trans)} chegaram certas no receptor ideal")
    if problemas:
        rel("  Causas encontradas:")
        for pr in dict.fromkeys(problemas):
            rel(f"   - {pr}")
    elif trans and acertos == len(trans):
        rel("  Tudo certo neste teste.")
    rel("=" * 70)
    return problemas


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="Diagnostico da transmissao por som entre 2 PCs.")
    ap.add_argument("--versao", default="v4", help="pasta do protocolo a testar (v3, v4...)")
    ap.add_argument("--perfil", default="padrao", help="perfil de temporizacao (padrao, jogo)")
    ap.add_argument("--hex", default=HEX_TESTE, help="mensagem de teste em hex (igual nos 2 PCs)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="lista dispositivos de audio e testa 48 kHz mono")
    s.add_argument("--entrada", type=int)
    s.add_argument("--saida", type=int)

    s = sub.add_parser("emitir", help="toca a mensagem de teste")
    s.add_argument("--repeticoes", type=int, default=3)
    s.add_argument("--intervalo", type=float, default=4.0, help="segundos entre repeticoes")
    s.add_argument("--saida", type=int, help="indice do dispositivo de saida (ver 'info')")

    s = sub.add_parser("receber", help="grava, salva o .wav e analisa")
    s.add_argument("--duracao", type=float, default=60.0)
    s.add_argument("--entrada", type=int, help="indice do dispositivo de entrada (ver 'info')")

    s = sub.add_parser("analisar", help="analisa um .wav gravado antes")
    s.add_argument("arquivo")

    s = sub.add_parser("simular", help="gera uma gravacao sintetica e analisa (sem hardware)")
    s.add_argument("--nivel", type=float, default=3000, help="pico do sinal no microfone (int16)")
    s.add_argument("--ruido-rms", type=float, default=60, help="ruido rosa de sala (int16 rms)")
    s.add_argument("--reverb", type=float, default=0.0, help="duracao do eco da sala, em s")
    s.add_argument("--repeticoes", type=int, default=3)
    s.add_argument("--intervalo", type=float, default=4.0)
    s.add_argument("--seed", type=int, default=0)

    args = ap.parse_args()
    carregar_lib(args.versao, args.perfil)
    try:
        {"info": cmd_info, "emitir": cmd_emitir, "receber": cmd_receber,
         "analisar": cmd_analisar, "simular": cmd_simular}[args.cmd](args)
    except RuntimeError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()

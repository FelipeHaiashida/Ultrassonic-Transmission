"""
Compara a v4 e a v5 sob o MESMO canal simulado.

    python comparar_v4.py                                   # cenarios sinteticos
    python comparar_v4.py --sala ../gravacao_20261001_145753.wav
    python comparar_v4.py --tentativas 40 --texto "Vamos jogar"

Para cada cenario, manda o mesmo texto N vezes (ruido diferente a cada vez)
por cada versao e conta quantas vezes a mensagem chega identica. As duas
versoes passam pelo mesmo caminho: gera o audio, aplica o canal, entrega ao
receptor em streaming, decodifica.

Para a comparacao ser justa, as duas emitem no MESMO pico (o da v4, 55% do
fundo de escala). A v5 pode emitir mais alto sem distorcer (--nivel-v5), o que
e uma vantagem a mais, mas separada da do protocolo.

Isto e simulacao (veja canal_sim.py): vale para comparar as versoes entre si,
nao para prever o que o seu alto-falante e o seu microfone entregam.
"""

import argparse
import importlib.util
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
V4 = os.path.join(AQUI, '..', 'v4', 'transfer_lib.py')

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def _carregar_v4():
    spec = importlib.util.spec_from_file_location('transfer_lib_v4', V4)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cenarios(sala, extremos=False):
    """[(nome, fabrica)] - fabrica(rng) devolve o canal. Importa canal_sim aqui
    dentro porque os processos filhos precisam recriar tudo do zero."""
    import canal_sim as cs
    lista = [
        ("sala em silencio",            lambda r: (lambda x: x)),
        ("ruido branco  10 dB",         lambda r: cs.ruido_branco(10, r)),
        ("ruido branco   0 dB",         lambda r: cs.ruido_branco(0, r)),
        ("ruido branco  -6 dB",         lambda r: cs.ruido_branco(-6, r)),
        ("ruido branco -12 dB",         lambda r: cs.ruido_branco(-12, r)),
        ("reverberacao 0,5 s",          lambda r: cs.reverb(0.5, rng=r)),
        ("apito 3 kHz a -10 dB",        lambda r: cs.interferente(3000, -10, r)),
        ("apito 6,5 kHz a -10 dB",      lambda r: cs.interferente(6500, -10, r)),
        ("6 rajadas de 0,3 s a -6 dB",  lambda r: cs.rajadas(6, 0.3, -6, rng=r)),
        ("deriva de clock +100 ppm",    lambda r: cs.deriva(100)),
        ("microfone saturando (+20 dB)", lambda r: cs.saturar(20)),
    ]
    if extremos:
        lista = [
            ("ruido branco -20 dB",          lambda r: cs.ruido_branco(-20, r)),
            ("ruido branco -26 dB",          lambda r: cs.ruido_branco(-26, r)),
            ("ruido branco -32 dB",          lambda r: cs.ruido_branco(-32, r)),
            ("ruido rosa, RMS 8000",         lambda r: cs.ruido_colorido(8000, 1.0, r)),
            ("ruido rosa, RMS 20000",        lambda r: cs.ruido_colorido(20000, 1.0, r)),
            ("apito 6,5 kHz a 0 dB",         lambda r: cs.interferente(6500, 0, r)),
            ("apito 6,5 kHz a +6 dB",        lambda r: cs.interferente(6500, 6, r)),
            ("reverberacao 1,0 s",           lambda r: cs.reverb(1.0, 1.0, r)),
            ("reverberacao 2,0 s",           lambda r: cs.reverb(2.0, 1.0, r)),
            ("12 rajadas de 0,5 s a 0 dB",   lambda r: cs.rajadas(12, 0.5, 0, rng=r)),
            ("deriva de clock +500 ppm",     lambda r: cs.deriva(500)),
        ]
    if sala:
        base, _ = cs.carregar_ruido(sala)
        for g in ((50, 60, 70) if extremos else (0, 10, 20, 30, 40)):
            lista.append((f"sala gravada +{g} dB",
                          lambda r, g=g: cs.ruido_gravado(base, g, r)))
        if not extremos:
            lista.append(("sala dificil: +20 dB + reverb + rajadas + sat.",
                          lambda r: cs.compor(cs.reverb(0.4, rng=r),
                                              cs.rajadas(4, 0.3, -8, rng=r),
                                              cs.ruido_gravado(base, 20, r),
                                              cs.saturar(6))))
    return lista


def _rodar(versao, texto, canal, nivel_v5, perfil):
    """Emite `texto` pela versao dada, aplica o canal, recebe. (texto_recebido, segundos_de_audio)."""
    if versao == 'v4':
        tl = _carregar_v4()
        tl.usar_perfil(perfil)
        sinal = tl.normalize_audio(tl.text_to_signal(texto))
        dur_s = len(sinal) / tl.Fs
        x = np.concatenate((sinal, np.zeros(int(3.0 * tl.Fs), dtype=np.int16)))
        x = canal(x)
        got = tl.signal_to_text(tl._coletar(tl._blocos_do_array(x), verbose=False))
        return got, dur_s
    sys.path.insert(0, AQUI)
    import transfer_lib as tl
    tl.NIVEL_TX = nivel_v5
    tl.usar_perfil(perfil)
    sinal = tl.text_to_signal(texto)
    dur_s = len(sinal) / tl.Fs
    got = tl.signal_to_text(tl.loopback(sinal, canal=canal))
    return got, dur_s


def tentativa(args):
    idx, versao, seed, texto, sala, nivel_v5, extremos, perfil = args
    sys.path.insert(0, AQUI)
    rng = np.random.default_rng(1000 + seed)
    nome, fabrica = cenarios(sala, extremos)[idx]
    try:
        got, dur_s = _rodar(versao, texto, fabrica(rng), nivel_v5, perfil)
    except Exception as e:                      # um erro de codigo conta como falha, mas aparece
        print(f"   [erro em {versao}/{nome}] {type(e).__name__}: {e}", flush=True)
        got, dur_s = None, 0.0
    return idx, versao, got == texto, dur_s


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--sala', help="WAV com o ruido de uma sala real (ex.: gravacao do diagnostico)")
    ap.add_argument('--tentativas', type=int, default=20)
    ap.add_argument('--texto', default="Ola mundo")
    ap.add_argument('--nivel-v5', type=float, default=18000 / 32767,
                    help="pico da v5, fracao do fundo de escala (padrao: igual ao da v4)")
    ap.add_argument('--extremos', action='store_true',
                    help="cenarios bem alem do razoavel, para achar onde cada versao quebra")
    ap.add_argument('--perfil', default='padrao', choices=['padrao', 'jogo'],
                    help="perfil de temporizacao, aplicado as duas versoes")
    ap.add_argument('--so', help="roda so os cenarios cujo nome contem este texto")
    args = ap.parse_args()

    sala = os.path.abspath(args.sala) if args.sala else None
    lista = cenarios(sala, args.extremos)
    escolhidos = [i for i, (n, _) in enumerate(lista) if not args.so or args.so in n]
    tarefas = [(i, v, s, args.texto, sala, args.nivel_v5, args.extremos, args.perfil)
               for i in escolhidos for v in ('v4', 'v5') for s in range(args.tentativas)]

    print(f"Texto {args.texto!r} ({len(args.texto.encode())} bytes), perfil {args.perfil}, "
          f"{args.tentativas} tentativas por celula, {len(tarefas)} simulacoes...", flush=True)
    t0 = time.time()
    placar = {(i, v): [] for i in escolhidos for v in ('v4', 'v5')}
    duracao = {}
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
        for idx, versao, ok, dur_s in ex.map(tentativa, tarefas, chunksize=2):
            placar[(idx, versao)].append(ok)
            if dur_s:
                duracao[versao] = dur_s

    print(f"\n{'cenario':<50} {'v4':>6} {'v5':>6}")
    print("-" * 64)
    for i in escolhidos:
        a = 100 * np.mean(placar[(i, 'v4')])
        b = 100 * np.mean(placar[(i, 'v5')])
        print(f"{lista[i][0]:<50} {a:>5.0f}% {b:>5.0f}%")
    print("-" * 64)
    print(f"duracao do audio: v4 {duracao.get('v4', 0):.1f} s, v5 {duracao.get('v5', 0):.1f} s "
          f"  (simulado em {time.time() - t0:.0f} s)")


if __name__ == '__main__':
    main()

"""
Mede quanto do preâmbulo dá para cortar antes do protocolo quebrar.

    python perfil_sweep.py

O preâmbulo é fixo por transmissão (warmup + sync + midgap + cooldown), então
domina o custo de mensagens curtas. Numa resposta de 1 byte são 5,5s de
preâmbulo para 0,8s de dados. Este script varre cada parâmetro isoladamente e
mede a taxa de acerto, para que o perfil 'jogo' saia de dados e não de chute.

Uma ressalva honesta: WARMUP não é mensurável aqui. Ele é silêncio puro, então
o loopback sempre vai aprovar qualquer valor, inclusive zero. Ele existe porque
placas de áudio cortam os primeiros milissegundos de reprodução - um problema
de hardware que só teste em hardware resolve.
"""

import sys

import numpy as np

import transfer_lib as tl

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

CURTA = "B3"                    # tiro do jogo
LONGA = "Vamos para a praia"    # 36 tons, 35 intervalos entre notas
REPETICOES = 24


def taxa(msg, snr=None, **overrides):
    """% de mensagens que voltam idênticas, com os parâmetros sobrescritos."""
    antes = {k: getattr(tl, k) for k in overrides}
    for k, v in overrides.items():
        setattr(tl, k, v)
    try:
        sinal = tl.text_to_signal(msg)
        ok = 0
        for s in range(REPETICOES):
            rng = np.random.default_rng(s)
            recebido = tl.signal_to_text(tl.loopback(sinal, ruido_db=snr, rng=rng))
            ok += (recebido == msg)
        return round(100 * ok / REPETICOES)
    finally:
        for k, v in antes.items():
            setattr(tl, k, v)


def varrer(titulo, parametro, valores, msg, nota, **fixos):
    print(f"\n{titulo}\n" + "-" * len(titulo))
    print(f"  {nota}")
    print(f"\n  {parametro:>8}   limpo   30dB   20dB")
    for v in valores:
        linha = [taxa(msg, snr=snr, **{parametro: v}, **fixos)
                 for snr in (None, 30, 20)]
        marca = "" if linha[0] == 100 else "   <-- quebra"
        print(f"  {v:>8}   {linha[0]:>4}%  {linha[1]:>4}%  {linha[2]:>4}%{marca}")


def main():
    print("=" * 62)
    print("  VARREDURA DE PREÂMBULO")
    print("=" * 62)
    tl.usar_perfil("padrao")
    print(f"  Perfil padrão: preâmbulo de {tl.preambulo_s():.1f}s por transmissão")

    varrer("1. Duração do sync", "syncDur",
           [1.5, 1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2], CURTA,
           "O detector precisa de tom suficiente para reconhecer o sync (1 kHz).")

    varrer("2. Silêncio entre sync e dados", "MIDGAP",
           [0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1, 0.05], CURTA,
           "Curto demais e a cauda do sync invade o primeiro tom de dado.",
           syncDur=0.6)

    varrer("3. Silêncio que encerra a recepção", "FIM_SILENCIO",
           [2.5, 1.5, 1.0, 0.8, 0.6, 0.4, 0.3, 0.2], LONGA,
           f"Precisa ser maior que o intervalo entre notas ({tl.gap}s), senão o "
           f"receptor corta no meio da mensagem.",
           syncDur=0.6, MIDGAP=0.25)

    # --- comparação final ---
    print("\n4. Perfis lado a lado\n" + "-" * 21)
    print(f"\n  {'perfil':<9} {'preâmbulo':>10} {'tiro B3':>9} {'resposta X':>11}"
          f" {'limpo':>7} {'30dB':>6} {'20dB':>6}")
    for nome in ("padrao", "jogo"):
        tl.usar_perfil(nome)
        pre = tl.preambulo_s()
        t_curto = len(tl.text_to_signal("B3")) / tl.Fs
        t_resp = len(tl.text_to_signal("X")) / tl.Fs
        limpo, s30, s20 = (taxa(CURTA, snr=snr) for snr in (None, 30, 20))
        print(f"  {nome:<9} {pre:>9.1f}s {t_curto:>8.1f}s {t_resp:>10.1f}s"
              f" {limpo:>6}% {s30:>5}% {s20:>5}%")

    # custo de uma partida inteira
    print("\n  Partida de ~19 jogadas (2 transmissões cada):")
    for nome in ("padrao", "jogo"):
        tl.usar_perfil(nome)
        por_jogada = (len(tl.text_to_signal("B3")) + len(tl.text_to_signal("X"))) / tl.Fs
        espera = 2 * tl.FIM_SILENCIO  # o receptor ainda espera o silêncio final
        total = 19 * (por_jogada + espera)
        print(f"    {nome:<9} {total / 60:>5.1f} min")

    tl.usar_perfil("padrao")
    print("\n" + "=" * 62)
    print("  WARMUP não aparece aqui: é silêncio, o loopback aprova qualquer")
    print("  valor. Só teste em hardware diz quanto ele pode encolher.")
    print("=" * 62)


if __name__ == "__main__":
    main()

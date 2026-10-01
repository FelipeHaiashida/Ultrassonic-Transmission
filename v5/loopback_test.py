"""
Testes do protocolo v5, sem microfone e sem alto-falante.

    python loopback_test.py            # suite completa (~2 min)
    python loopback_test.py --rapido   # so o essencial (~20 s)

Tudo roda em memoria pelo MESMO receptor em streaming do modo audio. Alem do
que a v4 testava (ida e volta, alinhamento, ruido), esta suite exercita o que
a v5 acrescenta: o corretor de erros, o gatilho por chirp (sem disparo falso
em ruido puro, com recuperacao de disparo falso) e canais deformados por
reverberacao, interferencia, rajadas, saturacao e deriva de clock.
"""

import argparse
import random
import sys

import numpy as np

import canal_sim as cs
import reed_solomon as rs
import transfer_lib as tl
from batalha_naval import CPU, Tabuleiro, fmt_coord, VITORIA

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

falhas = []


def checar(condicao, descricao, detalhe=""):
    marca = "ok  " if condicao else "FALHA"
    print(f"  [{marca}] {descricao}" + (f"   {detalhe}" if detalhe else ""))
    if not condicao:
        falhas.append(descricao)


def secao(titulo):
    print(f"\n{titulo}\n" + "-" * len(titulo))


def taxa(texto, n, canal_de_seed, perfil=None):
    """% de n envios de `texto` que chegam identicos. canal_de_seed(rng) -> canal."""
    sinal = tl.text_to_signal(texto)
    ok = 0
    for s in range(n):
        rng = np.random.default_rng(s)
        ok += tl.signal_to_text(tl.loopback(sinal, canal=canal_de_seed(rng))) == texto
    return round(100 * ok / n)


# ---------------------------------------------------------------------------

def teste_reed_solomon():
    secao("Reed-Solomon (isolado)")
    rng = random.Random(7)
    dentro = fora_detectado = fora_errado = 0
    for _ in range(300):
        nsym = rng.choice([4, 6, 8, 12])
        msg = [rng.randrange(256) for _ in range(rng.randint(0, 40))]
        cw = rs.codificar(msg, nsym)
        n = len(cw)
        f = rng.randint(0, nsym)
        e = rng.randint(0, (nsym - f) // 2)
        pos = rng.sample(range(n), min(n, f + e))
        rx = cw[:]
        for p in pos:
            rx[p] ^= rng.randint(1, 255)
        try:
            out, _ = rs.decodificar(rx, nsym, pos[:f])
            dentro += out == msg
        except rs.ErroRS:
            pass
    checar(dentro == 300, "dentro da capacidade (2*erros + apagamentos <= paridade) sempre corrige",
           f"{dentro}/300")
    for _ in range(300):
        nsym = 4
        msg = [rng.randrange(256) for _ in range(rng.randint(1, 20))]
        cw = rs.codificar(msg, nsym)
        rx = cw[:]
        for p in rng.sample(range(len(cw)), 3):          # 3 erros > 2 corrigiveis
            rx[p] ^= rng.randint(1, 255)
        try:
            out, _ = rs.decodificar(rx, nsym)
            fora_errado += out != msg
        except rs.ErroRS:
            fora_detectado += 1
    checar(fora_errado <= 3, "alem da capacidade, quase sempre recusa em vez de devolver lixo",
           f"{fora_detectado} recusados, {fora_errado} errados em 300")


def teste_ida_e_volta():
    secao("Ida e volta pelo canal limpo")
    casos = [
        ("coordenada de jogo", "B3"),
        ("resposta de 1 byte", "X"),
        ("frase da v1", "Vamos para a praia"),
        ("acentos em utf-8", "ação, coração"),
        ("payload vazio", ""),
    ]
    for nome, msg in casos:
        recebido = tl.signal_to_text(tl.loopback(tl.text_to_signal(msg)))
        checar(recebido == msg, nome, f"{len(msg.encode())} bytes")


def teste_bytes_binarios():
    secao("Bytes binarios, ate o maximo por transmissao")
    dados = bytes(range(tl.MAX_PAYLOAD))
    recebido = tl.signal_to_bytes(tl.loopback(tl.bytes_to_signal(dados)))
    checar(recebido == dados, f"{tl.MAX_PAYLOAD} bytes (o maximo), valores 0 a {tl.MAX_PAYLOAD - 1}",
           f"{len(recebido)}/{len(dados)} bytes, {tl.tempo_s(len(dados)) / 60:.1f} min de audio")
    try:
        tl.bytes_to_signal(bytes(tl.MAX_PAYLOAD + 1))
        checar(False, "recusa payload maior que o maximo")
    except ValueError:
        checar(True, "recusa payload maior que o maximo")


def _slot_inicio(k):
    return int(tl.WARMUP * tl.Fs) + int(tl.CHIRP_DUR * tl.Fs) + int(tl.GUARD * tl.Fs) + k * tl._passo()


def _corromper(sinal, slots):
    """Troca o tom de cada slot por outro (um dos digitos, deslocado)."""
    s = sinal.copy()
    for k in slots:
        a = _slot_inicio(k)
        n = int(tl.dur * tl.Fs)
        ref = np.max(np.abs(s[a:a + n]))
        # o digito errado e o vizinho: o erro mais traicoeiro, tom claro e no lugar de outro
        f_errada = tl.fbase + tl.step_hz * ((k * 7 + 3) % 16)
        t = np.arange(n) / tl.Fs
        s[a:a + n] = ref * np.sin(2 * np.pi * f_errada * t)
    return s


def teste_correcao_de_erros():
    secao("Correcao de erros no sinal")
    msg = "Ola mundo"
    p = len(msg.encode())
    sinal = tl.text_to_signal(msg)
    nsym = tl._nsym(p)
    n_slots = 2 * tl._n_palavra(p)
    rng = random.Random(3)

    def tentar(qtd_bytes):
        bytes_ruins = rng.sample(range(tl._n_palavra(p)), qtd_bytes)
        slots = [2 * b for b in bytes_ruins]            # um nibble de cada byte basta para estraga-lo
        return tl.signal_to_text(tl.loopback(_corromper(sinal, slots)))

    for q in range(0, nsym // 2 + 1):
        checar(tentar(q) == msg, f"{q} byte(s) com tom trocado de {tl._n_palavra(p)}",
               f"paridade de {nsym} B corrige ate {nsym // 2}")
    recusados = 0
    errados = 0
    for _ in range(6):
        r = tentar(nsym // 2 + 3)
        recusados += r == ""
        errados += r not in ("", msg)
    checar(errados == 0, "dano alem da capacidade nao vira texto errado",
           f"{recusados}/6 recusados, {errados} entregaram lixo")


def teste_alinhamento():
    secao("Alinhamento: o receptor ja estava ouvindo")
    msg = "B3"
    sinal = tl.text_to_signal(msg)
    for antes in (0.0, 0.3, 1.7, 6.0):
        r = tl.signal_to_text(tl.loopback(sinal, canal=cs.silencio_antes(antes)))
        checar(r == msg, f"{antes} s de silencio antes da transmissao", f"recebeu {r!r}")
    # gravacao que comeca no meio do silencio inicial (a placa de som acorda tarde)
    base = tl.normalize_audio(sinal)
    cortes = (0.2, 0.6, 0.9)
    for corte in cortes:
        r = tl.signal_to_text(base[int(corte * tl.Fs):])
        checar(r == msg, f"gravacao comecando {corte} s depois do inicio do audio", f"recebeu {r!r}")


def teste_vazio_e_bordas():
    secao("Casos de borda")
    checar(tl.signal_to_text(np.zeros(tl.Fs * 3)) == "", "silencio puro devolve vazio")
    checar(tl.signal_to_text(np.zeros(100)) == "", "gravacao curtissima nao quebra")
    ruido = np.random.default_rng(0).normal(0, 3000, tl.Fs * 5)
    checar(tl.signal_to_text(ruido) == "", "ruido puro devolve vazio, nao lixo")
    checar(tl.decodificar(ruido)["ok"] is False, "ruido puro e reportado como falha")


def teste_streaming():
    secao("Receptor em streaming")
    # duas mensagens seguidas no mesmo fluxo de microfone
    a, b = "B3", "Vamos"
    sa = tl.normalize_audio(tl.text_to_signal(a))
    sb = tl.normalize_audio(tl.text_to_signal(b))
    pausa = np.zeros(int(0.7 * tl.Fs), dtype=np.int16)
    fluxo = tl._blocos_do_array(np.concatenate((sa, pausa, sb, np.zeros(tl.Fs, dtype=np.int16))))
    r1 = tl.signal_to_text(tl._coletar(fluxo, verbose=False))
    r2 = tl.signal_to_text(tl._coletar(fluxo, verbose=False))
    checar((r1, r2) == (a, b), "duas transmissoes seguidas, uma recepcao cada",
           f"recebeu {r1!r} e {r2!r}")

    # disparo falso: um chirp de inicio solto, sem fim, e depois a mensagem de verdade
    falso = tl.normalize_audio(np.concatenate((np.zeros(int(0.4 * tl.Fs)), tl._chirp_sobe(),
                                               np.zeros(int(3 * tl.Fs)))))
    real = tl.normalize_audio(tl.text_to_signal("Vamos"))
    fluxo = tl._blocos_do_array(np.concatenate((falso, real, np.zeros(tl.Fs, dtype=np.int16))))
    r = tl.signal_to_text(tl._coletar(fluxo, verbose=False))
    checar(r == "Vamos", "disparo falso (chirp sem fim) nao engole a mensagem seguinte", f"recebeu {r!r}")


def teste_disparo_falso():
    secao("Sem disparo falso em ruido puro (60 s de cada)")
    rng = np.random.default_rng(5)
    n = 60 * tl.Fs
    ref = np.zeros(n, dtype=np.int16)
    ref[0] = 26000
    casos = [
        ("ruido branco forte (RMS 8000)", lambda: rng.normal(0, 8000, n)),
        ("ruido rosa forte (RMS 8000)", lambda: cs.ruido_colorido(8000, 1.0, rng)(np.zeros(n, dtype=np.int16))),
        ("30 rajadas de 0,3 s a 0 dB", lambda: cs.rajadas(30, 0.3, 0, rng=rng)(ref) + rng.normal(0, 100, n)),
        ("apito em 6,5 kHz a 0 dB", lambda: 26000 * np.sin(2 * np.pi * 6500 * np.arange(n) / tl.Fs)
                                            + rng.normal(0, 300, n)),
    ]
    for nome, gerar in casos:
        x = np.clip(np.asarray(gerar(), dtype=np.float64), -32768, 32767).astype(np.int16)
        info = {}
        tl._coletar(tl._blocos_do_array(x), verbose=False, info=info)
        checar(info["motivo"] == "sync nao detectado", nome, f"({info['motivo']})")


def teste_ruido_branco(n):
    secao("Taxa de acerto por relacao sinal/ruido (ruido branco, banda cheia)")
    print("     SNR    acerto")
    res = {}
    for snr in (10, 0, -10, -15, -20):
        res[snr] = taxa("Vamos para a praia", n, lambda r, snr=snr: cs.ruido_branco(snr, r))
        print(f"     {snr:>3} dB  {res[snr]:>4}%")
    checar(res[10] == 100 and res[0] == 100, "canal limpo e medio (10 e 0 dB): sempre")
    checar(res[-10] >= 90, "ruido 10 dB MAIS FORTE que o sinal (-10 dB): >= 90%", f"{res[-10]}%")
    checar(res[-15] >= 90, "ruido 15 dB mais forte (-15 dB): >= 90%", f"{res[-15]}%")


def teste_canais(n, sala):
    secao("Canais deformados")
    base = cs.carregar_ruido(sala)[0] if sala else None
    casos = [
        ("reverberacao 0,5 s",                lambda r: cs.reverb(0.5, rng=r)),
        ("reverberacao 1,0 s (sala vazia)",   lambda r: cs.reverb(1.0, 1.0, r)),
        ("apito em 3 kHz a -10 dB",           lambda r: cs.interferente(3000, -10, r)),
        ("apito em 6,5 kHz a -10 dB (na faixa)", lambda r: cs.interferente(6500, -10, r)),
        ("apito em 6,5 kHz a 0 dB (na faixa)",  lambda r: cs.interferente(6500, 0, r)),
        ("6 rajadas de 0,3 s a -6 dB",        lambda r: cs.rajadas(6, 0.3, -6, rng=r)),
        ("ruido rosa forte (RMS 8000)",       lambda r: cs.ruido_colorido(8000, 1.0, r)),
        ("deriva de clock +150 ppm",          lambda r: cs.deriva(150)),
        ("deriva de clock -150 ppm",          lambda r: cs.deriva(-150)),
        ("microfone saturando (+20 dB)",      lambda r: cs.saturar(20)),
    ]
    if base is not None:
        casos += [
            ("sua sala gravada, volume original", lambda r: cs.ruido_gravado(base, 0, r)),
            ("sua sala gravada +30 dB",           lambda r: cs.ruido_gravado(base, 30, r)),
            ("sala dificil: +20 dB, reverb, rajadas, saturacao",
             lambda r: cs.compor(cs.reverb(0.4, rng=r), cs.rajadas(4, 0.3, -8, rng=r),
                                 cs.ruido_gravado(base, 20, r), cs.saturar(6))),
        ]
    for nome, fabrica in casos:
        t = taxa("Ola mundo", n, fabrica)
        checar(t >= 90, nome, f"{t}%")
    if base is None:
        print("     (sem --sala: cenarios com a gravacao da sala real foram pulados)")


def teste_perfil_jogo():
    secao("Perfil 'jogo' (temporizacao curta)")
    tl.usar_perfil("jogo")
    try:
        for msg in ("B3", "X"):
            r = tl.signal_to_text(tl.loopback(tl.text_to_signal(msg)))
            checar(r == msg, f"{msg!r} no canal limpo",
                   f"{len(tl.text_to_signal(msg)) / tl.Fs:.1f} s de audio")
        t = taxa("B3", 8, lambda r: cs.ruido_branco(-10, r))
        checar(t >= 90, "ruido 10 dB mais forte que o sinal (-10 dB)", f"{t}%")
    finally:
        tl.usar_perfil("padrao")


def teste_partida_completa(ruido_db=None):
    quando = "canal limpo" if ruido_db is None else f"ruido branco a {ruido_db} dB"
    secao(f"Partida completa de Batalha Naval pelo canal ({quando})")
    rng = random.Random(42)
    tabuleiro = Tabuleiro(rng)
    cpu = CPU(rng)
    canal_rng = np.random.default_rng(0)
    canal = None if ruido_db is None else cs.ruido_branco(ruido_db, canal_rng)

    jogadas = 0
    venceu = False
    tl.usar_perfil("jogo")
    try:
        while jogadas < 36:
            cel = cpu.escolher()
            jogadas += 1
            tiro = tl.signal_to_text(tl.loopback(tl.text_to_signal(fmt_coord(cel)), canal=canal))
            if tiro != fmt_coord(cel):
                checar(False, "tiro chegou integro", f"{fmt_coord(cel)!r} -> {tiro!r}")
                return
            resultado = tabuleiro.receber_tiro(cel)
            resposta = tl.signal_to_text(tl.loopback(tl.text_to_signal(resultado), canal=canal))
            if resposta != resultado:
                checar(False, "resposta chegou integra", f"{resultado!r} -> {resposta!r}")
                return
            cpu.informar(cel, resultado)
            if resposta == VITORIA:
                venceu = True
                break
    finally:
        tl.usar_perfil("padrao")
    checar(venceu, "partida terminou em vitoria", f"{jogadas} jogadas, {jogadas * 2} transmissoes")
    checar(tabuleiro.derrotado(), "todos os navios afundados")


def teste_custo():
    secao("Custo do canal")
    for perfil in ("padrao", "jogo"):
        tl.usar_perfil(perfil)
        print(f"     perfil {perfil}: preambulo {tl.preambulo_s():.1f} s fixos;  "
              f"tiro 'B3' {tl.tempo_s(2):.1f} s;  resposta 'X' {tl.tempo_s(1):.1f} s;  "
              f"18 bytes {tl.tempo_s(18):.1f} s")
    tl.usar_perfil("padrao")
    util = 1 / (2 * (tl.dur + tl.gap) * (1 + tl.PARIDADE))
    print(f"     taxa util em mensagens longas: {util:.2f} bytes/s "
          f"(v4: 1,25 bytes/s sem nenhuma correcao de erro)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapido", action="store_true", help="so o essencial")
    ap.add_argument("--sala", help="WAV com ruido de sala real (ex.: gravacao do diagnostico)")
    args = ap.parse_args()
    n = 4 if args.rapido else 10

    print("=" * 62)
    print("  TESTES v5 EM LOOPBACK - sem microfone, sem alto-falante")
    print("=" * 62)

    teste_reed_solomon()
    teste_ida_e_volta()
    teste_vazio_e_bordas()
    teste_correcao_de_erros()
    teste_alinhamento()
    teste_streaming()
    teste_ruido_branco(n)
    if not args.rapido:
        teste_bytes_binarios()
        teste_disparo_falso()
        teste_canais(n, args.sala)
        teste_perfil_jogo()
        teste_partida_completa()
        teste_partida_completa(ruido_db=-10)
    teste_custo()

    print("\n" + "=" * 62)
    if falhas:
        print(f"  {len(falhas)} FALHA(S): " + "; ".join(falhas))
        sys.exit(1)
    print("  Tudo passou.")
    print("=" * 62)


if __name__ == "__main__":
    main()

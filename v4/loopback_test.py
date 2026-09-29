"""
Testes do protocolo, sem microfone e sem alto-falante.

    python loopback_test.py

Tudo aqui roda em memória: gera o sinal, passa pelo detector de sync e pelo
decodificador de verdade. É o mesmo caminho de código do modo áudio, então um
teste verde aqui significa que o protocolo está certo - só falta o ar.

Antes disso, testar qualquer mudança exigia dois computadores, uma sala em
silêncio e uns cinco minutos de cerimônia. Agora custa dois segundos.
"""

import random
import sys

import numpy as np

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


# ---------------------------------------------------------------------------

def teste_ida_e_volta():
    secao("Ida e volta pelo canal limpo")
    casos = [
        ("coordenada de jogo", "B3"),
        ("resposta de 1 byte", "X"),
        ("frase da v1", "Vamos para a praia"),
        ("acentos em utf-8", "ação, coração"),
    ]
    for nome, msg in casos:
        recebido = tl.signal_to_text(tl.loopback(tl.text_to_signal(msg)))
        checar(recebido == msg, nome, f"{len(msg.encode())} bytes")


def teste_bytes_binarios():
    secao("Bytes binários (não só texto)")
    dados = bytes(range(256))
    # 256 bytes levam ~3.5 min de áudio, bem acima do teto padrão de 30s.
    recebido = tl.signal_to_bytes(tl.loopback(tl.bytes_to_signal(dados), max_s=300))
    checar(recebido == dados, "256 bytes, todos os valores possíveis",
           f"{len(recebido)}/{len(dados)} bytes de volta")


def teste_teto_de_gravacao():
    """O teto de 30s é o limite prático de payload por transmissão. Melhor
    medir e documentar do que descobrir no meio de uma demo."""
    secao("Teto de gravação")
    dados = bytes(range(256))
    truncado = tl.signal_to_bytes(tl.loopback(tl.bytes_to_signal(dados)))
    checar(truncado == dados[:len(truncado)],
           "o que chega antes do teto chega correto",
           f"{len(truncado)} bytes íntegros")
    checar(30 <= len(truncado) <= 40,
           f"payload máximo por transmissão no teto padrão",
           f"{len(truncado)} bytes")


def teste_vazio():
    secao("Casos de borda")
    checar(tl.signal_to_text(tl.loopback(tl.text_to_signal(""))) == "",
           "payload vazio não quebra")
    checar(tl.signal_to_hex(np.zeros(tl.Fs)) == "",
           "silêncio puro devolve vazio")
    checar(tl.find_data_start(np.zeros(1000)) is None,
           "find_data_start devolve None sem sinal")


def teste_alinhamento():
    """A v1 assumia offset fixo de 2.0s depois do sync. Aqui empurramos o
    início da gravação para conferir que a busca dinâmica aguenta."""
    secao("Robustez de alinhamento")
    msg = "B3"
    base = tl.normalize_audio(tl.text_to_signal(msg))
    for corte_ms in (0, 37, 113, 250, 480):
        corte = int(corte_ms / 1000 * tl.Fs)
        recebido = tl.signal_to_text(base[corte:])
        checar(recebido == msg, f"gravação começando {corte_ms}ms adiantada",
               f"recebeu {recebido!r}")


def teste_ruido():
    secao("Taxa de acerto por relação sinal/ruído")
    msg = "Vamos para a praia"
    sinal = tl.text_to_signal(msg)
    print("     SNR   acerto")
    resultados = {}
    for snr in (40, 30, 20, 15, 10):
        acertos = sum(
            tl.signal_to_text(tl.loopback(sinal, ruido_db=snr,
                                          rng=np.random.default_rng(s))) == msg
            for s in range(20)
        )
        resultados[snr] = acertos * 5
        print(f"     {snr:>2} dB   {acertos * 5:>3}%")
    checar(resultados[40] == 100, "canal limpo (40 dB) decodifica sempre")
    checar(resultados[30] >= 80, "canal bom (30 dB) acima de 80%",
           f"{resultados[30]}%")


def teste_partida_completa():
    """Joga uma partida inteira sem ninguém digitando: cada jogada passa pelo
    modem. Se o protocolo estiver errado, a partida trava aqui."""
    secao("Partida completa de Batalha Naval pelo canal")
    rng = random.Random(42)
    tabuleiro = Tabuleiro(rng)
    cpu = CPU(rng)

    jogadas = 0
    venceu = False
    while jogadas < 36:
        cel = cpu.escolher()
        jogadas += 1

        # o tiro vai pelo canal como texto
        tiro_recebido = tl.signal_to_text(tl.loopback(tl.text_to_signal(fmt_coord(cel))))
        if tiro_recebido != fmt_coord(cel):
            checar(False, "tiro chegou íntegro", f"{fmt_coord(cel)!r} -> {tiro_recebido!r}")
            return

        resultado = tabuleiro.receber_tiro(cel)

        # e a resposta volta pelo canal
        resposta = tl.signal_to_text(tl.loopback(tl.text_to_signal(resultado)))
        if resposta != resultado:
            checar(False, "resposta chegou íntegra", f"{resultado!r} -> {resposta!r}")
            return

        cpu.informar(cel, resultado)
        if resposta == VITORIA:
            venceu = True
            break

    checar(venceu, "partida terminou em vitória", f"{jogadas} jogadas")
    checar(tabuleiro.derrotado(), "todos os navios afundados")

    segundos = jogadas * 2 * (len(tl.text_to_signal('B3')) / tl.Fs)
    print(f"     {jogadas} jogadas x 2 transmissões = {segundos / 60:.1f} min de áudio real")


def teste_custo():
    secao("Custo do canal")
    por_byte = (tl.dur + tl.gap) * 2
    print(f"     {1 / por_byte:.2f} bytes/s úteis")
    for nome, texto in (("tiro 'B3'", "B3"), ("resposta 'X'", "X")):
        segundos = len(tl.text_to_signal(texto)) / tl.Fs
        overhead = tl.WARMUP + tl.syncDur + tl.MIDGAP + tl.COOLDOWN
        print(f"     {nome:<14} {segundos:>4.1f}s  "
              f"({overhead:.1f}s de preâmbulo + {segundos - overhead:.1f}s de dados)")


def main():
    print("=" * 58)
    print("  TESTES EM LOOPBACK - sem microfone, sem alto-falante")
    print("=" * 58)

    teste_ida_e_volta()
    teste_bytes_binarios()
    teste_teto_de_gravacao()
    teste_vazio()
    teste_alinhamento()
    teste_ruido()
    teste_partida_completa()
    teste_custo()

    print("\n" + "=" * 58)
    if falhas:
        print(f"  {len(falhas)} FALHA(S): " + "; ".join(falhas))
        sys.exit(1)
    print("  Tudo passou.")
    print("=" * 58)


if __name__ == "__main__":
    main()

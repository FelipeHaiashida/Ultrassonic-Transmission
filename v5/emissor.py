"""Gera o WAV com a mensagem (e, se pedido, ja toca).

    python emissor.py                          # "Vamos para a praia" -> transmissao.wav
    python emissor.py "B3" --tocar             # gera e toca pelo alto-falante
    python emissor.py "B3" --perfil jogo       # temporizacao curta (receptor tem que usar a mesma)

Toque com o receptor.py ja escutando na outra maquina.
"""

import argparse
import os
import sys

import transfer_lib as tl

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

ap = argparse.ArgumentParser(description="Gera (e toca) a transmissao de um texto.")
ap.add_argument("texto", nargs="?", default="Vamos para a praia")
ap.add_argument("--tocar", action="store_true", help="toca pelo alto-falante (precisa de sounddevice)")
ap.add_argument("--perfil", default="padrao", choices=list(tl.PERFIS))
ap.add_argument("--saida", default="transmissao.wav")
args = ap.parse_args()

tl.usar_perfil(args.perfil)
dados = args.texto.encode("utf-8")
if len(dados) > tl.MAX_PAYLOAD:
    sys.exit(f"Texto de {len(dados)} bytes; o maximo por transmissao e {tl.MAX_PAYLOAD}.")

print("--- EMISSOR ---")
sinal = tl.bytes_to_signal(dados)
tl.write_wav(sinal, args.saida)
print(f"1. {args.saida!r} gerado: {os.path.getsize(args.saida) / 1024:.0f} KB, "
      f"{len(sinal) / tl.Fs:.1f}s ({len(dados)} bytes + {tl._nsym(len(dados))} de paridade).")

# Confere no loopback se o proprio sinal decodifica, antes de montar dois computadores.
volta = tl.signal_to_text(tl.loopback(sinal))
if volta == args.texto:
    print("2. Verificado em loopback: decodifica corretamente.")
else:
    print(f"2. ATENCAO: em loopback voltou {volta!r}, nao {args.texto!r}.")

if args.tocar:
    print("3. Tocando...")
    try:
        tl.tocar(sinal)
    except RuntimeError as e:
        sys.exit(f"\n{e}")
    print("   Pronto.")
else:
    print("3. Toque o WAV, ou rode de novo com --tocar.")

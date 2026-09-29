"""Escuta o microfone e salva a mensagem recebida."""

import sys

import transfer_lib as tl

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

print("--- RECEPTOR ---")
print("Aguardando sinal de áudio... (Ctrl+C para sair)")

try:
    gravado = tl.gravar()
except RuntimeError as e:
    print(f"\n{e}")
    sys.exit(1)

texto = tl.writeSignalToFile(gravado, outputFile="mensagem_recebida.txt")
if texto:
    print("\nSUCESSO. Salvo em 'mensagem_recebida.txt'.")
else:
    print("\nNada foi decodificado. Tente com o volume mais alto ou mais perto.")

"""Gera o WAV com a mensagem. Toque ele com o receptor.py já escutando."""

import os
import sys

import transfer_lib as tl

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

TEXTO = "Vamos para a praia"

print("--- EMISSOR ---")
with open("mensagem.txt", "w", encoding="utf-8") as f:
    f.write(TEXTO)
print("1. Arquivo mensagem.txt criado.")

print("2. Gerando áudio...")
tl.writeFileToWav("mensagem.txt", outputFile="transmissao.wav")

tamanho = os.path.getsize("transmissao.wav")
segundos = len(tl.text_to_signal(TEXTO)) / tl.Fs
print(f"3. 'transmissao.wav' gerado: {tamanho / 1024:.1f} KB, {segundos:.1f}s.")

# Confere no loopback se o próprio arquivo decodifica, antes de você
# perder tempo montando dois computadores.
volta = tl.signal_to_text(tl.loopback(tl.text_to_signal(TEXTO)))
if volta == TEXTO:
    print("4. Verificado em loopback: decodifica corretamente.")
else:
    print(f"4. ATENÇÃO: em loopback voltou {volta!r}, não {TEXTO!r}.")

print("   -> Rode o receptor.py na outra máquina e depois toque o WAV.")

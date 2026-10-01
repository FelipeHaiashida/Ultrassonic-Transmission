"""Escuta o microfone e salva a mensagem recebida.

    python receptor.py
    python receptor.py --perfil jogo       # o mesmo perfil do emissor

A gravacao fica em ultima_gravacao.wav. Se nao decodificar, o arquivo permite
reanalisar sem repetir o teste:

    python receptor.py --arquivo ultima_gravacao.wav
"""

import argparse
import sys

from scipy.io import wavfile

import transfer_lib as tl

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

ap = argparse.ArgumentParser(description="Recebe uma transmissao pelo microfone.")
ap.add_argument("--perfil", default="padrao", choices=list(tl.PERFIS))
ap.add_argument("--arquivo", help="decodifica um WAV gravado, em vez de ouvir o microfone")
args = ap.parse_args()
tl.usar_perfil(args.perfil)

print("--- RECEPTOR ---")
if args.arquivo:
    fs, gravado = wavfile.read(args.arquivo)
    if gravado.ndim > 1:
        gravado = gravado[:, 0]
    if fs != tl.Fs:
        sys.exit(f"O WAV esta em {fs} Hz; o protocolo usa {tl.Fs} Hz.")
else:
    print("Aguardando sinal de audio... (Ctrl+C para sair)")
    try:
        gravado = tl.gravar()
    except RuntimeError as e:
        sys.exit(f"\n{e}")
    if len(gravado):
        wavfile.write("ultima_gravacao.wav", tl.Fs, gravado.astype("int16"))
        print("Gravacao salva em 'ultima_gravacao.wav'.")

r = tl.decodificar(gravado)
if r["ok"]:
    texto = r["payload"].decode("utf-8", errors="replace")
    with open("mensagem_recebida.txt", "w", encoding="utf-8") as f:
        f.write(texto)
    print(f"\nTEXTO: {texto}")
    print(f"Qualidade: chirps {r['rho_sobe']:.2f} / {r['rho_desce']:.2f} (de 0 a 1); "
          f"{r['corrigidos']} byte(s) corrigido(s), {r['apagados']} marcado(s) como duvidoso(s).")
    print("SUCESSO. Salvo em 'mensagem_recebida.txt'.")
else:
    print(f"\nNada foi decodificado: {r['motivo']}.")
    if "rho_sobe" in r:
        print(f"(chirps achados: {r['rho_sobe']:.2f} / {r['rho_desce']:.2f}; "
              f"{r['n_simbolos']} tons entre eles)")
    print("Tente com o volume mais alto ou mais perto, e confira se os dois lados usam o mesmo --perfil.")

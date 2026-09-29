"""
Batalha Naval por som.

Cada jogada trafega inteiramente pelo canal sonoro (faixa audível, 1-3,5 kHz,
pensada para testes): a coordenada vai como texto ("B3", 2 bytes) e a
resposta volta como um único caractere. Nenhum dado passa por rede.

    python batalha_naval.py                     # contra a CPU, canal em loopback
    python batalha_naval.py --ouvir              # idem, mas tocando o áudio de cada jogada
    python batalha_naval.py --ruido 25           # idem, com canal degradado
    python batalha_naval.py --audio anfitriao    # 2 máquinas, áudio real
    python batalha_naval.py --audio convidado

O modo padrão não precisa de sounddevice nem de microfone: as jogadas passam pelo
modem de verdade (gera o áudio, detecta o sync, decodifica), só não passam
pelo ar. É o mesmo caminho de código do modo áudio.

Com --ouvir, cada jogada também é tocada de verdade no alto-falante (precisa de
sounddevice) - mas a decodificação continua vindo do loopback, então o jogo não
depende de um microfone captar o som de volta. É a forma mais simples de ouvir
o protocolo funcionando sem precisar de duas máquinas.

Protocolo:
    tiro      "B3"     coordenada, 2 bytes
    resposta  "A"      água
              "X"      acerto
              "D"      navio afundado
              "V"      último navio afundado, quem atirou venceu
"""

import argparse
import binascii
import random
import sys

import transfer_lib as tl

# O console do Windows nem sempre usa UTF-8, o que quebra os acentos.
for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

COLS = "ABCDEF"
N = 6
NAVIOS = [("Cruzador", 3), ("Fragata", 2), ("Patrulha", 2)]

AGUA, ACERTO, AFUNDOU, VITORIA = "A", "X", "D", "V"


# ------------------------------------------------------------- coordenadas

def parse_coord(s):
    s = (s or "").strip().upper().replace(" ", "")
    if len(s) != 2 or s[0] not in COLS or not s[1].isdigit():
        return None
    linha = int(s[1])
    if not 1 <= linha <= N:
        return None
    return (COLS.index(s[0]), linha - 1)


def fmt_coord(cel):
    return f"{COLS[cel[0]]}{cel[1] + 1}"


# --------------------------------------------------------------- tabuleiro

class Tabuleiro:
    """O tabuleiro de um jogador: onde estão os navios dele e o que já levou tiro."""

    def __init__(self, rng):
        self.navios = []
        self.tiros_recebidos = set()
        for nome, tamanho in NAVIOS:
            self._posicionar(nome, tamanho, rng)

    def _posicionar(self, nome, tamanho, rng):
        ocupadas = self.ocupadas()
        while True:
            if rng.random() < 0.5:
                c, l = rng.randrange(N - tamanho + 1), rng.randrange(N)
                celulas = {(c + i, l) for i in range(tamanho)}
            else:
                c, l = rng.randrange(N), rng.randrange(N - tamanho + 1)
                celulas = {(c, l + i) for i in range(tamanho)}
            if not celulas & ocupadas:
                self.navios.append({"nome": nome, "celulas": celulas, "atingidas": set()})
                return

    def ocupadas(self):
        return {cel for n in self.navios for cel in n["celulas"]}

    def receber_tiro(self, cel):
        self.tiros_recebidos.add(cel)
        for navio in self.navios:
            if cel in navio["celulas"]:
                navio["atingidas"].add(cel)
                if navio["atingidas"] == navio["celulas"]:
                    return VITORIA if self.derrotado() else AFUNDOU
                return ACERTO
        return AGUA

    def derrotado(self):
        return all(n["atingidas"] == n["celulas"] for n in self.navios)

    def restantes(self):
        return sum(1 for n in self.navios if n["atingidas"] != n["celulas"])


# ---------------------------------------------------------------- desenho

def _grade_propria(tab):
    ocupadas = tab.ocupadas()
    linhas = []
    for l in range(N):
        celulas = []
        for c in range(N):
            cel = (c, l)
            if cel in tab.tiros_recebidos:
                celulas.append("X" if cel in ocupadas else "o")
            else:
                celulas.append("#" if cel in ocupadas else ".")
        linhas.append(celulas)
    return linhas


def _grade_mira(mira):
    simbolos = {AGUA: "o", ACERTO: "X", AFUNDOU: "*", VITORIA: "*"}
    return [[simbolos.get(mira.get((c, l)), ".") for c in range(N)] for l in range(N)]


def desenhar(tab, mira):
    esq, dir_ = _grade_propria(tab), _grade_mira(mira)
    cab = " ".join(COLS)
    print()
    print(f"   SEU TABULEIRO        TIROS NO INIMIGO")
    print(f"   {cab}            {cab}")
    for l in range(N):
        print(f" {l + 1} {' '.join(esq[l])}         {l + 1} {' '.join(dir_[l])}")
    print("   # navio  X acerto  o água  * afundado")
    print()


# ------------------------------------------------------------------ canal

def transmitir_loopback(texto, ruido_db=None, rng=None, tocar=False):
    """Manda o texto pelo modem e devolve o que saiu do outro lado.

    Passa por geração de áudio, detecção de sync e decodificação de verdade -
    se o modem errar, o jogo erra junto. É de propósito.

    tocar: se True, além de decodificar via loopback, toca o áudio de verdade
    no alto-falante (precisa de sounddevice). A decodificação não depende
    desse som ser captado de volta - ele é só para você ouvir o protocolo
    funcionando.
    """
    sinal = tl.text_to_signal(texto)
    if tocar:
        print(f"    [modem] tocando {len(sinal) / tl.Fs:.1f}s de áudio...")
        tl.tocar(sinal)
    recebido = tl.signal_to_text(tl.loopback(sinal, ruido_db=ruido_db, rng=rng))
    hexs = binascii.hexlify(texto.encode()).decode()
    marca = "ok" if recebido == texto else f"ERRO, chegou {recebido!r}"
    print(f"    [modem] {texto!r}  hex {hexs}  {len(sinal) / tl.Fs:.1f}s de áudio  {marca}")
    return recebido


# --------------------------------------------------------------------- CPU

class CPU:
    """Atira ao acaso até acertar; depois insiste nas células vizinhas."""

    def __init__(self, rng):
        self.rng = rng
        self.tentadas = set()
        self.pendentes = []

    def escolher(self):
        while self.pendentes:
            cel = self.pendentes.pop(0)
            if cel not in self.tentadas:
                self.tentadas.add(cel)
                return cel
        livres = [(c, l) for c in range(N) for l in range(N) if (c, l) not in self.tentadas]
        cel = self.rng.choice(livres)
        self.tentadas.add(cel)
        return cel

    def informar(self, cel, resultado):
        if resultado != ACERTO:
            return
        c, l = cel
        for viz in ((c + 1, l), (c - 1, l), (c, l + 1), (c, l - 1)):
            if 0 <= viz[0] < N and 0 <= viz[1] < N and viz not in self.tentadas:
                self.pendentes.append(viz)


def descrever(resultado):
    return {AGUA: "água", ACERTO: "acerto!", AFUNDOU: "afundou um navio!",
            VITORIA: "afundou o último navio!"}.get(resultado, "resposta ininteligível")


# ---------------------------------------------------------- modo loopback

def jogar_contra_cpu(ruido_db=None, seed=None, tocar=False):
    rng = random.Random(seed)
    ruido_rng = None
    if ruido_db is not None:
        import numpy as np
        ruido_rng = np.random.default_rng(seed if seed is not None else 0)

    meu, dele = Tabuleiro(rng), Tabuleiro(rng)
    minha_mira = {}
    cpu = CPU(rng)

    print("=" * 58)
    subtitulo = "canal em loopback, tocando o áudio" if tocar else "canal em loopback"
    print(f"  BATALHA NAVAL SONORA (v4, faixa audível) - {subtitulo}")
    print("=" * 58)
    print(f"  {len(NAVIOS)} navios de cada lado: " +
          ", ".join(f"{n} ({t})" for n, t in NAVIOS))
    if ruido_db is not None:
        print(f"  Canal degradado a {ruido_db} dB de SNR - espere jogadas perdidas.")
    if tocar:
        print("  Cada jogada vai tocar de verdade no alto-falante - pode aumentar o volume.")
    print("  Digite uma coordenada tipo B3, ou 'sair'.")

    while True:
        desenhar(meu, minha_mira)
        print(f"  Você: {meu.restantes()} navios | Inimigo: {dele.restantes()} navios")

        alvo = None
        while alvo is None:
            entrada = input("  Seu tiro: ")
            if entrada.strip().lower() in ("sair", "quit", "q"):
                print("  Até a próxima.")
                return
            alvo = parse_coord(entrada)
            if alvo is None:
                print(f"  Coordenada inválida. Use letra {COLS[0]}-{COLS[-1]} e número 1-{N}.")
            elif alvo in minha_mira:
                print("  Você já atirou aí.")
                alvo = None

        print(f"\n  >> transmitindo o tiro {fmt_coord(alvo)}")
        recebido = transmitir_loopback(fmt_coord(alvo), ruido_db, ruido_rng, tocar)
        alvo_recebido = parse_coord(recebido)
        if alvo_recebido is None:
            print("  O inimigo não entendeu o tiro. Jogada perdida.\n")
            continue

        resultado = dele.receber_tiro(alvo_recebido)
        print(f"  << inimigo respondendo")
        resposta = transmitir_loopback(resultado, ruido_db, ruido_rng, tocar)
        if resposta not in (AGUA, ACERTO, AFUNDOU, VITORIA):
            print("  Resposta chegou corrompida. Você não soube o resultado.\n")
            continue

        minha_mira[alvo_recebido] = resposta
        print(f"  {fmt_coord(alvo_recebido)}: {descrever(resposta)}\n")
        if resposta == VITORIA:
            desenhar(meu, minha_mira)
            print("  VOCÊ VENCEU. Toda a partida passou por som.\n")
            return

        # --- vez da CPU ---
        cel = cpu.escolher()
        print(f"  >> inimigo atirando em {fmt_coord(cel)}")
        recebido = transmitir_loopback(fmt_coord(cel), ruido_db, ruido_rng, tocar)
        cel_recebida = parse_coord(recebido)
        if cel_recebida is None:
            print("  Você não entendeu o tiro do inimigo. Jogada perdida.\n")
            continue

        resultado = meu.receber_tiro(cel_recebida)
        print(f"  << você respondendo")
        transmitir_loopback(resultado, ruido_db, ruido_rng, tocar)
        cpu.informar(cel_recebida, resultado)
        print(f"  Inimigo em {fmt_coord(cel_recebida)}: {descrever(resultado)}\n")
        if resultado == VITORIA:
            desenhar(meu, minha_mira)
            print("  O INIMIGO VENCEU.\n")
            return


# ------------------------------------------------------------- modo áudio

def jogar_por_audio(papel, seed=None):
    rng = random.Random(seed)
    meu = Tabuleiro(rng)
    minha_mira = {}
    primeiro = papel == "anfitriao"

    print("=" * 58)
    print(f"  BATALHA NAVAL SONORA (v4, faixa audível) - áudio real ({papel})")
    print("=" * 58)
    print("  Deixe as duas máquinas próximas, volume alto, sala em silêncio.")
    print(f"  {'Você atira primeiro.' if primeiro else 'O anfitrião atira primeiro.'}")

    def meu_turno():
        desenhar(meu, minha_mira)
        alvo = None
        while alvo is None:
            alvo = parse_coord(input("  Seu tiro: "))
            if alvo is None or alvo in minha_mira:
                print("  Coordenada inválida ou já usada.")
                alvo = None
        print(f"  Transmitindo {fmt_coord(alvo)}... (não faça barulho)")
        tl.enviar_texto(fmt_coord(alvo))
        print("  Aguardando a resposta...")
        resposta = tl.receber_texto()
        if resposta not in (AGUA, ACERTO, AFUNDOU, VITORIA):
            print(f"  Resposta ininteligível ({resposta!r}). Jogada perdida.")
            return False
        minha_mira[alvo] = resposta
        print(f"  {fmt_coord(alvo)}: {descrever(resposta)}")
        return resposta == VITORIA

    def turno_dele():
        print("\n  Aguardando o tiro do adversário...")
        cel = parse_coord(tl.receber_texto())
        if cel is None:
            print("  Não entendi o tiro. Jogada perdida.")
            return False
        resultado = meu.receber_tiro(cel)
        print(f"  Ele atirou em {fmt_coord(cel)}: {descrever(resultado)}")
        print("  Respondendo...")
        tl.enviar_texto(resultado)
        return resultado == VITORIA

    while True:
        if primeiro:
            if meu_turno():
                print("\n  VOCÊ VENCEU.\n")
                return
            if turno_dele():
                print("\n  O ADVERSÁRIO VENCEU.\n")
                return
        else:
            if turno_dele():
                print("\n  O ADVERSÁRIO VENCEU.\n")
                return
            if meu_turno():
                print("\n  VOCÊ VENCEU.\n")
                return


def main():
    ap = argparse.ArgumentParser(description="Batalha Naval transmitida por som (faixa audível de teste).")
    ap.add_argument("--audio", choices=["anfitriao", "convidado"],
                    help="jogar entre duas máquinas por áudio real (precisa de sounddevice)")
    ap.add_argument("--ouvir", action="store_true",
                    help="no modo contra a CPU, toca o áudio de cada jogada no alto-falante "
                         "(precisa de sounddevice) - a decodificação continua via loopback")
    ap.add_argument("--ruido", type=float, metavar="dB",
                    help="degrada o canal do loopback nessa SNR (ex: 25)")
    ap.add_argument("--seed", type=int, help="semente, para partidas reproduzíveis")
    args = ap.parse_args()

    try:
        if args.audio:
            jogar_por_audio(args.audio, args.seed)
        else:
            jogar_contra_cpu(args.ruido, args.seed, args.ouvir)
    except (KeyboardInterrupt, EOFError):
        print("\n  Encerrado.")
    except RuntimeError as e:
        print(f"\n  {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

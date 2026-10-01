"""
Batalha Naval por som - interface gráfica.

Mesmo jogo e mesmo protocolo de batalha_naval.py (de onde vêm as regras, o
tabuleiro e a CPU), com janela em vez de terminal:

    python batalha_naval_gui.py

Ao abrir, a janela pergunta o modo:

    contra a CPU   jogadas passam pelo modem em loopback (sem microfone);
                   opcionalmente toca o áudio de cada jogada e degrada o canal
    anfitrião      duas máquinas, áudio real; você atira primeiro
    convidado      duas máquinas, áudio real; o anfitrião atira primeiro

Clique numa casa do tabuleiro da direita para atirar. O painel do modem mostra
o que está trafegando: a transmissão tocando, o microfone ouvindo (com o nível
de entrada e o tom do protocolo que ele reconhece) e o registro de cada
mensagem, com o hex e se chegou inteira.

Se o adversário não responder (a mensagem se perdeu no ar), "Parar de ouvir"
encerra a espera e conta a jogada como perdida, como o modo terminal faz com
uma mensagem ininteligível.

Usa só tkinter, que já vem com o Python. sounddevice só é necessário para o
áudio real e para a opção de ouvir as jogadas.
"""

import binascii
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

import batalha_naval as bn
import transfer_lib as tl

VERSAO = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
RESPOSTAS = (bn.AGUA, bn.ACERTO, bn.AFUNDOU, bn.VITORIA)

COR = {
    "fundo":    "#0b1726",
    "painel":   "#11233a",
    "borda":    "#1d3654",
    "mar":      "#16365a",
    "mar_hover": "#2a5c8e",
    "texto":    "#e6eef7",
    "fraco":    "#8aa4c0",
    "navio":    "#9aa9ba",
    "acerto":   "#e5484d",
    "afundado": "#7a1f22",
    "agua":     "#4fa3e0",
    "destaque": "#f2c14e",
    "ok":       "#4cc38a",
}

FONTE = "Segoe UI" if sys.platform == "win32" else "Helvetica"
MONO = "Consolas" if sys.platform == "win32" else "Courier"


class Cancelado(Exception):
    """A partida foi abandonada (nova partida ou janela fechada) no meio de uma jogada."""


# ----------------------------------------------------------------- partida

class Partida:
    """Estado de uma partida. As jogadas rodam numa thread, que só mexe neste
    objeto - nunca no da janela. Assim, uma partida abandonada no meio de uma
    transmissão não suja a seguinte."""

    def __init__(self, modo, ouvir=False, ruido_db=None, seed=None):
        self.modo = modo
        self.ouvir = ouvir
        self.ruido_db = ruido_db
        self.rng = random.Random(seed)
        self.ruido_rng = np.random.default_rng(seed) if ruido_db is not None else None

        self.meu = bn.Tabuleiro(self.rng)
        self.dele = bn.Tabuleiro(self.rng) if modo == "cpu" else None
        self.cpu = bn.CPU(self.rng) if modo == "cpu" else None
        self.mira = {}
        self.ultimo_meu = None      # último tiro recebido
        self.ultimo_mira = None     # último tiro dado
        self.iniciou = False        # depois do primeiro tiro, os navios não mudam mais
        self.fim = None             # "vitoria" / "derrota"

        self.trava = threading.Lock()
        self.encerrada = threading.Event()
        self.pular = threading.Event()

    def checar(self):
        if self.encerrada.is_set():
            raise Cancelado


# ---------------------------------------------------------------- tabuleiro

class Grade(tk.Canvas):
    """Um tabuleiro 6x6 desenhado num Canvas."""

    def __init__(self, master, cel_px, ao_clicar=None):
        self.c = cel_px
        self.m = int(cel_px * 0.55)
        lado = self.m + bn.N * self.c + 1
        super().__init__(master, width=lado, height=lado, bg=COR["painel"],
                         highlightthickness=0)
        self.ao_clicar = ao_clicar
        self.ativa = False
        self.estados = {}
        self.ultimo = None
        self.sob_mouse = None
        self.bind("<Motion>", self._mover)
        self.bind("<Leave>", lambda e: self._mover(None))
        self.bind("<Button-1>", self._clique)
        self.desenhar()

    def _cel_em(self, x, y):
        c, l = (x - self.m) // self.c, (y - self.m) // self.c
        if 0 <= c < bn.N and 0 <= l < bn.N:
            return int(c), int(l)
        return None

    def _mover(self, ev):
        cel = self._cel_em(ev.x, ev.y) if ev else None
        if cel != self.sob_mouse:
            self.sob_mouse = cel
            if self.ativa:
                self.desenhar()

    def _clique(self, ev):
        cel = self._cel_em(ev.x, ev.y)
        if cel and self.ativa and self.ao_clicar:
            self.ao_clicar(cel)

    def atualizar(self, estados, ultimo=None, ativa=None):
        self.estados = estados
        self.ultimo = ultimo
        if ativa is not None:
            self.ativa = ativa
            self.config(cursor="crosshair" if ativa else "")
        self.desenhar()

    def desenhar(self):
        self.delete("all")
        c, m = self.c, self.m
        rotulo = (FONTE, max(9, c // 5), "bold")
        for i in range(bn.N):
            meio = m + i * c + c / 2
            self.create_text(meio, m / 2, text=bn.COLS[i], fill=COR["fraco"], font=rotulo)
            self.create_text(m / 2, meio, text=str(i + 1), fill=COR["fraco"], font=rotulo)

        for col in range(bn.N):
            for lin in range(bn.N):
                cel = (col, lin)
                x0, y0 = m + col * c, m + lin * c
                x1, y1 = x0 + c, y0 + c
                est = self.estados.get(cel, "vazio")
                mira = self.ativa and self.sob_mouse == cel and est in ("vazio",)
                self.create_rectangle(x0, y0, x1, y1, outline=COR["painel"], width=2,
                                      fill=COR["mar_hover"] if mira else COR["mar"])
                self._marca(est, x0, y0, x1, y1)
                if cel == self.ultimo:
                    self.create_rectangle(x0 + 2, y0 + 2, x1 - 2, y1 - 2,
                                          outline=COR["destaque"], width=3)

    def _marca(self, est, x0, y0, x1, y1):
        c = self.c
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ins = c * 0.14

        def x_marca(cor, larg):
            d = c * 0.24
            self.create_line(cx - d, cy - d, cx + d, cy + d, fill=cor, width=larg, capstyle="round")
            self.create_line(cx - d, cy + d, cx + d, cy - d, fill=cor, width=larg, capstyle="round")

        if est in ("navio", "atingido"):
            self.create_rectangle(x0 + ins, y0 + ins, x1 - ins, y1 - ins,
                                  fill=COR["navio"], outline="")
            if est == "atingido":
                x_marca(COR["acerto"], max(3, c // 12))
        elif est == "afundado":
            self.create_rectangle(x0 + ins, y0 + ins, x1 - ins, y1 - ins,
                                  fill=COR["afundado"], outline=COR["acerto"], width=2)
            x_marca(COR["texto"], max(2, c // 16))
        elif est == "acerto":
            r = c * 0.26
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=COR["acerto"], outline="")
        elif est == "agua":
            r = c * 0.11
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=COR["agua"], outline="")
        elif est == "oculto":
            # navio inimigo revelado no fim da partida contra a CPU
            self.create_rectangle(x0 + ins, y0 + ins, x1 - ins, y1 - ins,
                                  outline=COR["navio"], width=2, dash=(4, 3))


# ------------------------------------------------------------ nova partida

class DialogoNovaPartida(tk.Toplevel):
    def __init__(self, master, anterior):
        super().__init__(master, bg=COR["fundo"], padx=22, pady=18)
        self.title("Nova partida")
        self.resizable(False, False)
        self.resultado = None

        self.modo = tk.StringVar(value=anterior.get("modo", "cpu"))
        self.ouvir = tk.BooleanVar(value=anterior.get("ouvir", False))
        ruido = anterior.get("ruido_db")
        self.ruido = tk.StringVar(value="" if ruido is None else f"{ruido:g}")
        seed = anterior.get("seed")
        self.seed = tk.StringVar(value="" if seed is None else str(seed))

        def rotulo(texto, **kw):
            kw.setdefault("font", (FONTE, 10))
            kw.setdefault("fg", COR["texto"])
            return tk.Label(self, text=texto, bg=COR["fundo"], anchor="w", justify="left", **kw)

        rotulo("Como você quer jogar?", font=(FONTE, 12, "bold")).pack(fill="x", pady=(0, 8))
        modos = [
            ("cpu", "Contra a CPU",
             "As jogadas passam pelo modem em loopback. Não precisa de microfone."),
            ("anfitriao", "Duas máquinas: anfitrião",
             "Áudio real. Você atira primeiro."),
            ("convidado", "Duas máquinas: convidado",
             "Áudio real. O anfitrião atira primeiro."),
        ]
        for valor, titulo, descricao in modos:
            tk.Radiobutton(self, text=titulo, value=valor, variable=self.modo,
                           command=self._ajustar, bg=COR["fundo"], fg=COR["texto"],
                           selectcolor=COR["painel"], activebackground=COR["fundo"],
                           activeforeground=COR["texto"], font=(FONTE, 10, "bold"),
                           anchor="w").pack(fill="x")
            rotulo(descricao, fg=COR["fraco"], font=(FONTE, 9)).pack(fill="x", padx=(24, 0), pady=(0, 6))

        self.quadro_cpu = tk.Frame(self, bg=COR["fundo"])
        self.quadro_cpu.pack(fill="x", pady=(6, 0))
        self.chk_ouvir = tk.Checkbutton(
            self.quadro_cpu, text="Tocar o áudio de cada jogada no alto-falante",
            variable=self.ouvir, bg=COR["fundo"], fg=COR["texto"], selectcolor=COR["painel"],
            activebackground=COR["fundo"], activeforeground=COR["texto"], font=(FONTE, 10))
        self.chk_ouvir.pack(anchor="w")
        linha = tk.Frame(self.quadro_cpu, bg=COR["fundo"])
        linha.pack(fill="x", pady=(4, 0))
        tk.Label(linha, text="Ruído no canal (SNR em dB, vazio = sem ruído):", bg=COR["fundo"],
                 fg=COR["texto"], font=(FONTE, 10)).pack(side="left")
        self.ent_ruido = tk.Entry(linha, textvariable=self.ruido, width=6, font=(FONTE, 10))
        self.ent_ruido.pack(side="left", padx=6)

        linha = tk.Frame(self, bg=COR["fundo"])
        linha.pack(fill="x", pady=(10, 0))
        tk.Label(linha, text="Semente (opcional, para repetir a partida):", bg=COR["fundo"],
                 fg=COR["texto"], font=(FONTE, 10)).pack(side="left")
        tk.Entry(linha, textvariable=self.seed, width=8, font=(FONTE, 10)).pack(side="left", padx=6)

        botoes = tk.Frame(self, bg=COR["fundo"])
        botoes.pack(fill="x", pady=(16, 0))
        botao(botoes, "Começar", self._ok, primario=True).pack(side="right")
        botao(botoes, "Cancelar", self.destroy).pack(side="right", padx=8)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self._ajustar()
        self.transient(master)
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.focus_set()
        self.wait_window()

    def _ajustar(self):
        estado = "normal" if self.modo.get() == "cpu" else "disabled"
        self.chk_ouvir.config(state=estado)
        self.ent_ruido.config(state=estado)

    def _ok(self):
        modo = self.modo.get()
        ruido = seed = None
        try:
            if modo == "cpu" and self.ruido.get().strip():
                ruido = float(self.ruido.get().replace(",", "."))
            if self.seed.get().strip():
                seed = int(self.seed.get())
        except ValueError:
            messagebox.showerror("Nova partida", "Ruído e semente precisam ser números.", parent=self)
            return
        ouvir = modo == "cpu" and self.ouvir.get()
        if modo != "cpu" or ouvir:
            try:
                tl._sounddevice()
            except RuntimeError as e:
                messagebox.showerror("Nova partida", str(e), parent=self)
                return
        self.resultado = {"modo": modo, "ouvir": ouvir, "ruido_db": ruido, "seed": seed}
        self.destroy()


def botao(master, texto, comando, primario=False):
    fundo = COR["destaque"] if primario else COR["borda"]
    frente = COR["fundo"] if primario else COR["texto"]
    return tk.Button(master, text=texto, command=comando, bg=fundo, fg=frente,
                     activebackground=COR["mar_hover"], activeforeground=COR["texto"],
                     disabledforeground=COR["fraco"], relief="flat", bd=0, padx=14, pady=6,
                     font=(FONTE, 10, "bold"), cursor="hand2")


# ------------------------------------------------------------------ janela

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Batalha Naval Sonora ({VERSAO})")
        self.configure(bg=COR["fundo"])
        self.minsize(720, 560)

        escala = max(1.0, self.winfo_fpixels("1i") / 96)
        self.cel_px = int(46 * escala)

        self.p = None
        self.opcoes = {"modo": "cpu"}
        self.fila = queue.Queue()
        self.anim = None            # (início, duração) da transmissão em curso
        self.ouvindo_desde = None
        self.texto_fase = ""

        estilo = ttk.Style(self)
        estilo.theme_use("clam")
        estilo.configure("Modem.Horizontal.TProgressbar", troughcolor=COR["fundo"],
                         background=COR["destaque"], bordercolor=COR["painel"],
                         lightcolor=COR["destaque"], darkcolor=COR["destaque"])
        estilo.configure("Log.Vertical.TScrollbar", troughcolor=COR["painel"],
                         background=COR["borda"], bordercolor=COR["painel"],
                         arrowcolor=COR["fraco"])

        self._montar()
        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self.after(40, self._bombear)
        self.after(100, self._tique)
        self.after(150, self.nova_partida)

    # ---- montagem

    def _montar(self):
        topo = tk.Frame(self, bg=COR["fundo"], padx=18, pady=12)
        topo.pack(fill="x")
        tk.Label(topo, text="Batalha Naval Sonora", bg=COR["fundo"], fg=COR["texto"],
                 font=(FONTE, 18, "bold")).pack(side="left")
        self.lbl_modo = tk.Label(topo, text="", bg=COR["fundo"], fg=COR["fraco"], font=(FONTE, 10))
        self.lbl_modo.pack(side="left", padx=12, pady=(6, 0))
        self.btn_nova = botao(topo, "Nova partida", self.nova_partida, primario=True)
        self.btn_nova.pack(side="right")
        self.btn_sortear = botao(topo, "Sortear navios", self.sortear)
        self.btn_sortear.pack(side="right", padx=8)

        self.lbl_status = tk.Label(self, text="", bg=COR["painel"], fg=COR["texto"],
                                   font=(FONTE, 13, "bold"), pady=10)
        self.lbl_status.pack(fill="x", padx=18)

        meio = tk.Frame(self, bg=COR["fundo"], pady=12)
        meio.pack()
        self.grade_meu, self.lbl_frota = self._coluna(meio, "SEU TABULEIRO", None)
        self.grade_mira, self.lbl_inimigo = self._coluna(meio, "TIROS NO INIMIGO", self._atirar)

        legenda = tk.Frame(self, bg=COR["fundo"])
        legenda.pack()
        for cor, texto in ((COR["navio"], "navio"), (COR["acerto"], "acerto"),
                           (COR["afundado"], "afundado"), (COR["agua"], "água"),
                           (COR["destaque"], "último tiro")):
            tk.Label(legenda, text="■", fg=cor, bg=COR["fundo"], font=(FONTE, 12)).pack(side="left")
            tk.Label(legenda, text=texto, fg=COR["fraco"], bg=COR["fundo"],
                     font=(FONTE, 9)).pack(side="left", padx=(0, 12))

        modem = tk.Frame(self, bg=COR["painel"], padx=12, pady=10)
        modem.pack(fill="both", expand=True, padx=18, pady=(12, 18))

        linha = tk.Frame(modem, bg=COR["painel"])
        linha.pack(fill="x")
        tk.Label(linha, text="MODEM", bg=COR["painel"], fg=COR["fraco"],
                 font=(FONTE, 9, "bold")).pack(side="left")
        self.lbl_fase = tk.Label(linha, text="parado", bg=COR["painel"], fg=COR["texto"],
                                 font=(MONO, 10), anchor="w")
        self.lbl_fase.pack(side="left", padx=10, fill="x", expand=True)
        self.btn_parar = botao(linha, "Parar de ouvir", self._parar_de_ouvir)
        self.btn_parar.config(state="disabled")
        self.btn_parar.pack(side="right")

        linha = tk.Frame(modem, bg=COR["painel"])
        linha.pack(fill="x", pady=(8, 6))
        self.barra = ttk.Progressbar(linha, style="Modem.Horizontal.TProgressbar",
                                     mode="determinate", maximum=1000)
        self.barra.pack(side="left", fill="x", expand=True)
        tk.Label(linha, text="mic", bg=COR["painel"], fg=COR["fraco"],
                 font=(FONTE, 9)).pack(side="left", padx=(14, 4))
        self.medidor = tk.Canvas(linha, width=140, height=12, bg=COR["fundo"], highlightthickness=0)
        self.medidor.pack(side="left")
        self.lbl_tom = tk.Label(linha, text="", width=8, bg=COR["painel"], fg=COR["ok"],
                                font=(MONO, 9, "bold"))
        self.lbl_tom.pack(side="left", padx=(6, 0))

        quadro = tk.Frame(modem, bg=COR["painel"])
        quadro.pack(fill="both", expand=True)
        self.log = tk.Text(quadro, height=8, bg=COR["fundo"], fg=COR["texto"], font=(MONO, 9),
                           relief="flat", padx=8, pady=6, wrap="none", state="disabled")
        rolagem = ttk.Scrollbar(quadro, command=self.log.yview, style="Log.Vertical.TScrollbar")
        self.log.config(yscrollcommand=rolagem.set)
        rolagem.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, cor in (("tx", COR["destaque"]), ("rx", COR["agua"]), ("jogo", COR["texto"]),
                         ("erro", COR["acerto"]), ("aviso", COR["acerto"]),
                         ("hora", COR["fraco"]), ("fim", COR["ok"])):
            self.log.tag_config(tag, foreground=cor)

    def _coluna(self, master, titulo, ao_clicar):
        col = tk.Frame(master, bg=COR["painel"], padx=14, pady=10)
        col.pack(side="left", padx=9, anchor="n")
        tk.Label(col, text=titulo, bg=COR["painel"], fg=COR["fraco"],
                 font=(FONTE, 9, "bold")).pack(anchor="w")
        grade = Grade(col, self.cel_px, ao_clicar)
        grade.pack(pady=(6, 8))
        info = tk.Label(col, text="", bg=COR["painel"], fg=COR["texto"], font=(FONTE, 9),
                        justify="left", anchor="w")
        info.pack(fill="x")
        return grade, info

    # ---- ponte entre a thread da jogada e a janela (tkinter não é thread-safe)

    def _ui(self, p, fn, *args):
        self.fila.put((p, fn, args))

    def _bombear(self):
        try:
            while True:
                p, fn, args = self.fila.get_nowait()
                if p is self.p:
                    fn(*args)
        except queue.Empty:
            pass
        self.after(40, self._bombear)

    def _em_thread(self, p, alvo, *args):
        def corpo():
            try:
                alvo(p, *args)
            except Cancelado:
                pass
            except Exception as e:  # erro de áudio, dispositivo ausente etc.
                self._ui(p, self._erro, e)
        threading.Thread(target=corpo, daemon=True).start()

    # ---- partida

    def nova_partida(self):
        dlg = DialogoNovaPartida(self, self.opcoes)
        if dlg.resultado is None:
            if self.p is None:
                self._status("Clique em \"Nova partida\" para começar.", "fraco")
            return
        self.opcoes = dlg.resultado
        if self.p:
            self.p.encerrada.set()
        o = self.opcoes
        self.p = p = Partida(o["modo"], o["ouvir"], o["ruido_db"], o["seed"])

        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self._fase_parado()

        if p.modo == "cpu":
            extras = ["loopback"]
            if p.ouvir:
                extras.append("tocando o áudio")
            if p.ruido_db is not None:
                extras.append(f"ruído a {p.ruido_db:g} dB")
            self.lbl_modo.config(text=f"{VERSAO} · contra a CPU · " + ", ".join(extras))
            self._log(f"Partida contra a CPU. {len(bn.NAVIOS)} navios de cada lado: "
                      + ", ".join(f"{n} ({t})" for n, t in bn.NAVIOS) + ".", "jogo")
        else:
            papel = "anfitrião" if p.modo == "anfitriao" else "convidado"
            self.lbl_modo.config(text=f"{VERSAO} · áudio real · {papel}")
            self._log("Áudio real. Máquinas próximas, volume alto, sala em silêncio.", "jogo")
            self._log("As duas máquinas precisam abrir a partida antes do primeiro tiro.", "jogo")

        self._redesenhar()
        if p.modo == "convidado":
            self._em_thread(p, self._turno_dele_e_seguir)
        else:
            self._sua_vez_agora()

    def sortear(self):
        p = self.p
        if not p or p.iniciou:
            return
        with p.trava:
            p.meu = bn.Tabuleiro(p.rng)
        self._redesenhar()

    def _atirar(self, cel):
        p = self.p
        if not p or p.fim or not self.grade_mira.ativa or cel in p.mira:
            return
        p.iniciou = True
        p.ultimo_mira = cel
        self.grade_mira.atualizar(self.grade_mira.estados, cel, ativa=False)
        self.btn_sortear.config(state="disabled")
        alvo = self._rodada_cpu if p.modo == "cpu" else self._rodada_audio
        self._em_thread(p, alvo, cel)

    def _parar_de_ouvir(self):
        if self.p:
            self.p.pular.set()

    def _fechar(self):
        if self.p:
            self.p.encerrada.set()
        self.destroy()

    # ---- jogadas (rodam fora da thread da janela)

    def _rodada_cpu(self, p, alvo):
        coord = bn.fmt_coord(alvo)
        self._ui(p, self._status, f"Transmitindo o seu tiro em {coord}...", "ocupado")
        alvo_rec = bn.parse_coord(self._modem_loopback(p, coord, "você"))
        if alvo_rec is None:
            self._ui(p, self._log, "O inimigo não entendeu o tiro. Jogada perdida.", "aviso")
            return self._ui(p, self._sua_vez_agora)

        with p.trava:
            resultado = p.dele.receber_tiro(alvo_rec)
        self._ui(p, self._status, "O inimigo está respondendo...", "ocupado")
        resposta = self._modem_loopback(p, resultado, "inimigo")
        if resposta not in RESPOSTAS:
            self._ui(p, self._log, "A resposta chegou corrompida. Você não soube o resultado.", "aviso")
            return self._ui(p, self._sua_vez_agora)

        with p.trava:
            p.mira[alvo_rec] = resposta
            p.ultimo_mira = alvo_rec
        self._ui(p, self._redesenhar)
        self._ui(p, self._log, f"Seu tiro em {bn.fmt_coord(alvo_rec)}: {bn.descrever(resposta)}", "jogo")
        if resposta == bn.VITORIA:
            return self._ui(p, self._terminar, True)

        cel = p.cpu.escolher()
        self._ui(p, self._status, f"O inimigo atira em {bn.fmt_coord(cel)}...", "ocupado")
        cel_rec = bn.parse_coord(self._modem_loopback(p, bn.fmt_coord(cel), "inimigo"))
        if cel_rec is None:
            self._ui(p, self._log, "Você não entendeu o tiro do inimigo. Jogada perdida.", "aviso")
            return self._ui(p, self._sua_vez_agora)

        with p.trava:
            resultado = p.meu.receber_tiro(cel_rec)
            p.ultimo_meu = cel_rec
        self._ui(p, self._redesenhar)
        self._ui(p, self._status, "Você está respondendo...", "ocupado")
        self._modem_loopback(p, resultado, "você")
        p.cpu.informar(cel_rec, resultado)
        self._ui(p, self._log, f"Inimigo em {bn.fmt_coord(cel_rec)}: {bn.descrever(resultado)}", "jogo")
        if resultado == bn.VITORIA:
            return self._ui(p, self._terminar, False)
        self._ui(p, self._sua_vez_agora)

    def _rodada_audio(self, p, alvo):
        if self._meu_turno_audio(p, alvo):
            return self._ui(p, self._terminar, True)
        self._turno_dele_e_seguir(p)

    def _turno_dele_e_seguir(self, p):
        if self._turno_dele_audio(p):
            return self._ui(p, self._terminar, False)
        self._ui(p, self._sua_vez_agora)

    def _meu_turno_audio(self, p, alvo):
        coord = bn.fmt_coord(alvo)
        self._ui(p, self._status, f"Transmitindo {coord}... silêncio na sala", "ocupado")
        self._enviar(p, coord)
        self._ui(p, self._status, "Aguardando a resposta do adversário...", "ocupado")
        resposta = self._ouvir(p, "a resposta")
        if resposta not in RESPOSTAS:
            motivo = "sem resposta" if resposta is None else f"chegou {resposta!r}"
            self._ui(p, self._log, f"Resposta ininteligível ({motivo}). Jogada perdida.", "aviso")
            return False
        with p.trava:
            p.mira[alvo] = resposta
            p.ultimo_mira = alvo
        self._ui(p, self._redesenhar)
        self._ui(p, self._log, f"Seu tiro em {coord}: {bn.descrever(resposta)}", "jogo")
        return resposta == bn.VITORIA

    def _turno_dele_audio(self, p):
        self._ui(p, self._status, "Aguardando o tiro do adversário...", "espera")
        texto = self._ouvir(p, "o tiro do adversário")
        cel = bn.parse_coord(texto)
        if cel is None:
            motivo = "escuta interrompida" if texto is None else f"chegou {texto!r}"
            self._ui(p, self._log, f"Não entendi o tiro ({motivo}). Jogada perdida.", "aviso")
            return False
        with p.trava:
            p.iniciou = True
            resultado = p.meu.receber_tiro(cel)
            p.ultimo_meu = cel
        self._ui(p, self._redesenhar)
        self._ui(p, self._log, f"O adversário atirou em {bn.fmt_coord(cel)}: {bn.descrever(resultado)}", "jogo")
        self._ui(p, self._status, "Respondendo...", "ocupado")
        self._enviar(p, resultado)
        return resultado == bn.VITORIA

    # ---- canal

    def _modem_loopback(self, p, texto, quem):
        """Como transmitir_loopback do modo terminal: o texto passa pelo modem
        inteiro (modulação, sync, decodificação), só não passa pelo ar."""
        sinal = tl.text_to_signal(texto)
        dur = len(sinal) / tl.Fs
        if p.ouvir:
            self._ui(p, self._fase_tx, texto, dur)
            tl.tocar(sinal)
        else:
            self._ui(p, self._fase_ocupado, f"modulando e decodificando {texto!r}")
        p.checar()
        recebido = tl.signal_to_text(tl.loopback(sinal, ruido_db=p.ruido_db, rng=p.ruido_rng))
        p.checar()
        marca = "ok" if recebido == texto else f"ERRO, chegou {recebido!r}"
        self._ui(p, self._log, f"{quem:>7} → {texto!r:<5} hex {_hex(texto):<6} {dur:4.1f}s  {marca}",
                 "tx" if recebido == texto else "erro")
        self._ui(p, self._fase_parado)
        return recebido

    def _enviar(self, p, texto):
        sinal = tl.text_to_signal(texto)
        dur = len(sinal) / tl.Fs
        self._ui(p, self._fase_tx, texto, dur)
        tl.tocar(sinal)
        p.checar()
        self._ui(p, self._log, f"enviado  → {texto!r:<5} hex {_hex(texto):<6} {dur:4.1f}s", "tx")
        self._ui(p, self._fase_parado)

    def _ouvir(self, p, oque):
        """Como tl.gravar(), mas com saída: Parar de ouvir ou uma nova partida
        encerram a escuta. Devolve o texto decodificado, ou None se interrompida."""
        sd = tl._sounddevice()
        p.pular.clear()
        self._ui(p, self._fase_rx, oque)
        analisar = getattr(tl, "analisar_bloco", None)
        ultimo = [0.0]

        def blocos():
            while not (p.pular.is_set() or p.encerrada.is_set()):
                dados, _overflow = stream.read(tl.CHUNK)
                bloco = dados[:, 0]
                agora = time.monotonic()
                if agora - ultimo[0] >= 0.08:
                    ultimo[0] = agora
                    rms = float(np.sqrt(np.mean(bloco.astype(np.float64) ** 2)))
                    tom = None
                    if analisar:
                        tem_tom, eh_sync, freq = analisar(bloco)
                        tom = "SYNC" if eh_sync else (f"{freq:.0f}Hz" if tem_tom else None)
                    self._ui(p, self._nivel, rms, tom)
                yield bloco

        stream = sd.InputStream(samplerate=tl.Fs, channels=1, dtype="int16", blocksize=tl.CHUNK)
        inicio = time.monotonic()
        stream.start()
        try:
            info = {}
            sinal = tl._coletar(blocos(), verbose=False, info=info)
        finally:
            stream.stop()
            stream.close()
        self._ui(p, self._fase_parado)
        p.checar()
        if p.pular.is_set():
            p.pular.clear()
            self._ui(p, self._log, "Escuta interrompida por você.", "aviso")
            return None

        texto = tl.signal_to_text(sinal)
        motivo = info.get("motivo", "")
        self._ui(p, self._log, f"recebido ← {texto!r:<5} hex {_hex(texto):<6} "
                               f"{time.monotonic() - inicio:4.1f}s ouvindo  ({motivo})", "rx")
        return texto

    # ---- atualização da janela (thread da janela)

    def _redesenhar(self):
        p = self.p
        if not p:
            return
        with p.trava:
            meu = {}
            frota = []
            for navio in p.meu.navios:
                afundado = navio["atingidas"] == navio["celulas"]
                for cel in navio["celulas"]:
                    meu[cel] = ("afundado" if afundado else
                                "atingido" if cel in navio["atingidas"] else "navio")
                n, t = len(navio["atingidas"]), len(navio["celulas"])
                situacao = "afundado" if afundado else (f"atingido {n}/{t}" if n else "intacto")
                frota.append(f"{navio['nome']} ({t}): {situacao}")
            for cel in p.meu.tiros_recebidos:
                meu.setdefault(cel, "agua")

            simbolo = {bn.AGUA: "agua", bn.ACERTO: "acerto", bn.AFUNDOU: "afundado",
                       bn.VITORIA: "afundado"}
            mira = {cel: simbolo[r] for cel, r in p.mira.items()}
            if p.fim and p.dele:
                for cel in p.dele.ocupadas():
                    mira.setdefault(cel, "oculto")
            afundados = sum(1 for r in p.mira.values() if r in (bn.AFUNDOU, bn.VITORIA))
            acertos = sum(1 for r in p.mira.values() if r != bn.AGUA)
            tiros = len(p.mira)

        self.grade_meu.atualizar(meu, p.ultimo_meu)
        self.grade_mira.atualizar(mira, p.ultimo_mira)
        self.lbl_frota.config(text="\n".join(frota))
        self.lbl_inimigo.config(text=f"Navios inimigos afundados: {afundados} de {len(bn.NAVIOS)}\n"
                                     f"Tiros: {tiros}   acertos: {acertos}")
        self.btn_sortear.config(state="disabled" if p.iniciou or p.fim else "normal")

    def _sua_vez_agora(self):
        self._status("Sua vez: clique numa casa do tabuleiro inimigo", "vez")
        self._redesenhar()
        self.grade_mira.atualizar(self.grade_mira.estados, self.grade_mira.ultimo, ativa=True)

    def _terminar(self, venceu):
        p = self.p
        p.fim = "vitoria" if venceu else "derrota"
        self.grade_mira.atualizar(self.grade_mira.estados, self.grade_mira.ultimo, ativa=False)
        self._redesenhar()
        self._fase_parado()
        if venceu:
            self._status("VOCÊ VENCEU! A partida inteira passou por som.", "vitoria")
            self._log("Você venceu.", "fim")
        else:
            quem = "A CPU" if p.modo == "cpu" else "O adversário"
            self._status(f"{quem.upper()} VENCEU.", "derrota")
            self._log(f"{quem} venceu.", "aviso")

    def _erro(self, e):
        self._fase_parado()
        self._log(f"Erro: {e}", "erro")
        self._status("Erro no áudio. Veja o registro e comece uma nova partida.", "derrota")
        self.grade_mira.atualizar(self.grade_mira.estados, self.grade_mira.ultimo, ativa=False)
        messagebox.showerror("Batalha Naval Sonora", str(e), parent=self)

    def _status(self, texto, tom="ocupado"):
        cores = {"vez": (COR["destaque"], COR["fundo"]), "vitoria": (COR["ok"], COR["fundo"]),
                 "derrota": (COR["acerto"], COR["texto"]), "espera": (COR["borda"], COR["texto"]),
                 "fraco": (COR["painel"], COR["fraco"])}
        fundo, frente = cores.get(tom, (COR["painel"], COR["texto"]))
        self.lbl_status.config(text=texto, bg=fundo, fg=frente)

    def _log(self, texto, tag="jogo"):
        self.log.config(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S  "), "hora")
        self.log.insert("end", texto + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    # ---- painel do modem

    def _fase_tx(self, texto, dur):
        self.anim = (time.monotonic(), dur)
        self.ouvindo_desde = None
        self.texto_fase = f"TRANSMITINDO {texto!r} · {dur:.1f}s"
        self.barra.stop()
        self.barra.config(mode="determinate", value=0)
        self.btn_parar.config(state="disabled")

    def _fase_rx(self, oque):
        self.anim = None
        self.ouvindo_desde = time.monotonic()
        self.texto_fase = f"OUVINDO {oque}"
        self.barra.config(mode="indeterminate")
        self.barra.start(15)
        self.btn_parar.config(state="normal")

    def _fase_ocupado(self, texto):
        self.anim = None
        self.ouvindo_desde = None
        self.texto_fase = texto
        self.barra.config(mode="indeterminate")
        self.barra.start(15)
        self.btn_parar.config(state="disabled")

    def _fase_parado(self):
        self.anim = None
        self.ouvindo_desde = None
        self.texto_fase = "parado"
        self.barra.stop()
        self.barra.config(mode="determinate", value=0)
        self.btn_parar.config(state="disabled")
        self._nivel(0.0, None)

    def _nivel(self, rms, tom):
        db = 20 * np.log10(max(rms, 1.0) / 32768)          # dBFS, de -90 a 0
        frac = min(1.0, max(0.0, (db + 80) / 80))
        largura = int(self.medidor["width"])
        self.medidor.delete("all")
        cor = COR["acerto"] if frac > 0.92 else COR["ok"]
        self.medidor.create_rectangle(0, 0, int(largura * frac), 12, fill=cor, outline="")
        self.lbl_tom.config(text=tom or "")

    def _tique(self):
        texto = self.texto_fase
        if self.anim:
            ini, dur = self.anim
            decorrido = time.monotonic() - ini
            self.barra.config(value=min(1000, 1000 * decorrido / dur))
        elif self.ouvindo_desde:
            texto += f" · {time.monotonic() - self.ouvindo_desde:.0f}s"
        self.lbl_fase.config(text=texto)
        self.after(100, self._tique)


def _hex(texto):
    return binascii.hexlify((texto or "").encode()).decode()


def main():
    if sys.platform == "win32":
        try:  # texto nítido em telas com escala acima de 100%
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()

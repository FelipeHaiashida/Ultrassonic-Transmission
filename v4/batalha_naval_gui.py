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
de entrada, o espectro dos 17 tons do protocolo e o tom que ele reconhece) e o
registro de cada mensagem, com o hex e se chegou inteira.

No áudio real, uma mensagem pode se perder no ar. Para isso:

    Repetir tiro       enquanto você espera a resposta: toca o mesmo tiro de
                       novo e volta a ouvir (o adversário não ouviu o tiro)
    Repetir resposta   na sua vez, depois de responder: toca a resposta de
                       novo (o adversário não ouviu a sua resposta)
    Parar de ouvir     desiste de esperar e conta a jogada como perdida

Quem espera um tiro e ouve algo ilegível continua ouvindo, para dar tempo de o
tiro repetido chegar. E se a resposta ao seu tiro se perder de vez, o próximo
tiro do adversário é reconhecido como tiro e a partida segue sozinha.

O visual é pixel art: cada casa é uma peça de 12x12 pixels, ampliada conforme
a escala da tela, com as fontes Press Start 2P e VT323 (licença OFL, na pasta
fontes/). Sem elas, a janela cai em fontes do sistema.

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
import tkinter.font as tkfont
from tkinter import messagebox, ttk

import numpy as np

import batalha_naval as bn
import transfer_lib as tl

VERSAO = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
PASTA_FONTES = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
                            "fontes")
RESPOSTAS = (bn.AGUA, bn.ACERTO, bn.AFUNDOU, bn.VITORIA)
CURTA = {bn.AGUA: "água", bn.ACERTO: "acerto", bn.AFUNDOU: "afundou", bn.VITORIA: "vitória"}
REPETIR = object()          # _ouvir() interrompido por "Repetir tiro"
DICA_SEM_RESPOSTA_S = 15    # tiro + fim da recepção do outro lado + resposta ≈ 12 s
TOM_RECENTE_S = 3           # tom do protocolo ouvido há menos disso: algo está chegando

COR = {
    "fundo":       "#120f2b",
    "painel":      "#1d1a45",
    "borda":       "#2e2b66",
    "sombra":      "#070616",
    "poco":        "#0f0d24",
    "apagado":     "#15123a",
    "cartao":      "#17143a",
    "texto":       "#f4f1de",
    "fraco":       "#a3a6d6",
    "mar":         "#1f4e9c",
    "onda":        "#3d7fd6",
    "mar_mira":    "#2f6bc4",
    "onda_mira":   "#5a9be6",
    "ciano":       "#7fd4ff",
    "casco":       "#8b95a8",
    "casco_luz":   "#c9d1de",
    "casco_linha": "#2b3040",
    "fumaca":      "#4a5266",
    "afundado":    "#3a1426",
    "afundado_onda": "#55203a",
    "destroco":    "#6b2434",
    "destroco_linha": "#1a0710",
    "acerto":      "#e43b44",
    "fogo":        "#f77622",
    "brasa":       "#feae34",
    "destaque":    "#ffcd3f",
    "ok":          "#5ad16a",
    "alerta":      "#ff6b6b",
    "desab_fundo": "#24213f",
    "desab_texto": "#6d6a99",
    "desab_poco":  "#1a1838",
}

MONO = "Consolas" if sys.platform == "win32" else "Courier"
P = 4       # tamanho de um pixel da arte na tela; a escala da janela inteira sai dele
FT = {}     # fontes, criadas depois da janela (dependem de P)


def px(n):
    """Medida do protótipo (feito com P = 4) na escala atual."""
    return max(1, round(n * P / 4))


# ------------------------------------------------------------------ pixel art
# Cada peça tem 12x12 pixels: (x, y, largura, altura, cor). As peças de navio
# vêm em três partes, nas duas orientações: proa, meio e popa.

def _mar(base="mar", onda="onda", ondas=((2, 3), (4, 2), (7, 9), (9, 8))):
    return [(0, 0, 12, 12, base)] + [(x, y, 2, 1, onda) for x, y in ondas]


SPRITES = {
    "agua": _mar(),
    "agua_mira": _mar("mar_mira", "onda_mira"),
    "hl": _mar(ondas=((2, 3), (7, 9), (9, 8))) + [
        (3, 3, 9, 6, "casco_linha"), (2, 4, 1, 4, "casco_linha"), (1, 5, 1, 2, "casco_linha"),
        (3, 4, 9, 4, "casco"), (2, 5, 1, 2, "casco"), (3, 4, 9, 1, "casco_luz"),
        (7, 5, 2, 2, "casco_linha")],
    "hm": _mar(ondas=((7, 9), (9, 10))) + [
        (0, 3, 12, 6, "casco_linha"), (0, 4, 12, 4, "casco"), (0, 4, 12, 1, "casco_luz"),
        (4, 1, 4, 4, "casco_linha"), (5, 2, 2, 2, "casco"), (5, 2, 2, 1, "casco_luz")],
    "hr": _mar(ondas=((2, 1), (7, 10))) + [
        (0, 3, 10, 6, "casco_linha"), (10, 4, 1, 4, "casco_linha"), (0, 4, 10, 4, "casco"),
        (0, 4, 10, 1, "casco_luz"), (5, 5, 1, 2, "casco_linha"), (7, 5, 1, 2, "casco_linha"),
        (11, 5, 1, 2, "texto")],
    "vt": _mar(ondas=((9, 2), (1, 9))) + [
        (3, 3, 6, 9, "casco_linha"), (4, 2, 4, 1, "casco_linha"), (5, 1, 2, 1, "casco_linha"),
        (4, 3, 4, 9, "casco"), (5, 2, 2, 1, "casco"), (4, 3, 1, 9, "casco_luz"),
        (5, 7, 2, 2, "casco_linha")],
    "vm": _mar(ondas=((9, 2),)) + [
        (3, 0, 6, 12, "casco_linha"), (4, 0, 4, 12, "casco"), (4, 0, 1, 12, "casco_luz"),
        (1, 4, 4, 4, "casco_linha"), (2, 5, 2, 2, "casco"), (2, 5, 1, 2, "casco_luz")],
    "vb": _mar(ondas=((9, 3), (1, 8))) + [
        (3, 0, 6, 10, "casco_linha"), (4, 10, 4, 1, "casco_linha"), (4, 0, 4, 10, "casco"),
        (4, 0, 1, 10, "casco_luz"), (5, 5, 2, 1, "casco_linha"), (5, 7, 2, 1, "casco_linha"),
        (5, 11, 2, 1, "texto")],
    "boom": _mar(ondas=()) + [
        (5, 1, 2, 1, "acerto"), (5, 10, 2, 1, "acerto"), (1, 5, 1, 2, "acerto"),
        (10, 5, 1, 2, "acerto"), (2, 2, 1, 1, "acerto"), (9, 2, 1, 1, "acerto"),
        (2, 9, 1, 1, "acerto"), (9, 9, 1, 1, "acerto"), (3, 5, 6, 2, "fogo"),
        (5, 3, 2, 6, "fogo"), (4, 4, 4, 4, "fogo"), (4, 5, 4, 2, "brasa"),
        (5, 4, 2, 4, "brasa"), (5, 5, 2, 2, "texto")],
    "splash": _mar(ondas=()) + [
        (5, 2, 2, 1, "texto"), (5, 9, 2, 1, "texto"), (2, 5, 1, 2, "texto"), (9, 5, 1, 2, "texto"),
        (3, 3, 1, 1, "texto"), (8, 3, 1, 1, "texto"), (3, 8, 1, 1, "texto"), (8, 8, 1, 1, "texto"),
        (4, 4, 4, 1, "ciano"), (4, 7, 4, 1, "ciano"), (4, 5, 1, 2, "ciano"), (7, 5, 1, 2, "ciano"),
        (5, 5, 2, 2, "texto")],
    "wreck": _mar("afundado", "afundado_onda", ((2, 3), (4, 2))) + [
        (1, 7, 4, 2, "destroco"), (7, 8, 4, 2, "destroco"), (1, 9, 4, 1, "destroco_linha"),
        (7, 10, 4, 1, "destroco_linha"), (3, 5, 1, 2, "fogo"), (3, 6, 1, 1, "brasa"),
        (8, 5, 2, 2, "fumaca"), (9, 3, 1, 2, "fumaca"), (8, 1, 1, 2, "fumaca")],
    # navio inimigo revelado no fim da partida contra a CPU
    "oculto": _mar(ondas=()) + [
        (x, y, 2, 1, "casco") for y in (3, 8) for x in (1, 5, 9)] + [
        (1, 5, 1, 2, "casco"), (10, 5, 1, 2, "casco")],
    # efeitos por cima de uma peça
    "fogo": [
        (4, 5, 4, 3, "acerto"), (3, 6, 1, 2, "acerto"), (8, 6, 1, 2, "acerto"),
        (5, 3, 2, 4, "fogo"), (4, 6, 4, 1, "fogo"), (5, 5, 2, 2, "brasa"), (6, 3, 1, 1, "brasa"),
        (8, 1, 2, 1, "fumaca"), (9, 0, 1, 1, "fumaca"), (7, 2, 1, 1, "fumaca")],
    "ultimo": [
        (0, 0, 3, 1, "destaque"), (0, 0, 1, 3, "destaque"), (9, 0, 3, 1, "destaque"),
        (11, 0, 1, 3, "destaque"), (0, 11, 3, 1, "destaque"), (0, 9, 1, 3, "destaque"),
        (9, 11, 3, 1, "destaque"), (11, 9, 1, 3, "destaque")],
    "mira": [
        (5, 1, 2, 3, "destaque"), (5, 8, 2, 3, "destaque"), (1, 5, 3, 2, "destaque"),
        (8, 5, 3, 2, "destaque")],
    "logo": [
        (1, 5, 2, 3, "texto"), (3, 4, 1, 5, "texto"), (4, 3, 1, 7, "texto"), (5, 2, 1, 9, "texto"),
        (7, 5, 1, 3, "ciano"), (8, 4, 1, 1, "ciano"), (8, 8, 1, 1, "ciano"),
        (9, 2, 1, 2, "onda"), (10, 4, 1, 5, "onda"), (9, 9, 1, 2, "onda")],
}

# Ícones 8x8 da faixa de status, na cor do texto dela ("fundo" = cor do fundo).
ICONES = {
    "seta": [(2, 0, 1, 8), (3, 1, 1, 6), (4, 2, 1, 4), (5, 3, 1, 2)],
    "som": [(0, 3, 1, 2), (1, 2, 1, 4), (2, 1, 1, 6), (4, 3, 1, 2), (5, 2, 1, 1), (5, 5, 1, 1),
            (6, 1, 1, 1), (7, 2, 1, 4), (6, 6, 1, 1)],
    "ouvido": [(0, 4, 1, 2), (2, 2, 1, 5), (4, 0, 1, 8), (6, 3, 1, 3)],
    "coroa": [(3, 0, 2, 2), (0, 2, 8, 2), (1, 4, 6, 1), (1, 5, 2, 3), (5, 5, 2, 3)],
    "x": [(0, 0, 2, 2), (6, 0, 2, 2), (2, 2, 4, 4), (0, 6, 2, 2), (6, 6, 2, 2), (3, 3, 2, 2, "fundo")],
}


def sprite(cv, nome, x, y, p, tags=()):
    for sx, sy, w, h, cor in SPRITES[nome]:
        cv.create_rectangle(x + sx * p, y + sy * p, x + (sx + w) * p, y + (sy + h) * p,
                            fill=COR[cor], outline="", tags=tags)


def moldura(cv, x0, y0, x1, y1, face, luz=None, escuro=None):
    """Retângulo com a borda escura de 4 px e, se houver, o relevo: luz em
    cima e à esquerda, sombra embaixo e à direita."""
    b = px(4)
    cv.create_rectangle(x0, y0, x1, y1, fill=COR["sombra"], outline="")
    if luz:
        cv.create_rectangle(x0 + b, y0 + b, x1 - b, y1 - b, fill=luz, outline="")
        cv.create_rectangle(x0 + 2 * b, y0 + 2 * b, x1 - b, y1 - b, fill=escuro, outline="")
        cv.create_rectangle(x0 + 2 * b, y0 + 2 * b, x1 - 2 * b, y1 - 2 * b, fill=face, outline="")
    else:
        cv.create_rectangle(x0 + b, y0 + b, x1 - b, y1 - b, fill=face, outline="")


def caixa_marcar(cv, marcado, ativo=True):
    """Quadrado de rádio ou de marcar, como os do protótipo."""
    cv.delete("all")
    n, b = int(cv["width"]), px(4)
    cv.create_rectangle(0, 0, n, n, fill=COR["sombra"], outline="")
    if marcado:
        cv.create_rectangle(b, b, n - b, n - b, fill=COR["poco"], outline="")
        cv.create_rectangle(2 * b, 2 * b, n - 2 * b, n - 2 * b, outline="",
                            fill=COR["destaque"] if ativo else COR["desab_texto"])
    else:
        cv.create_rectangle(b, b, n - b, n - b, fill=COR["borda"] if ativo else COR["desab_poco"],
                            outline="")
        cv.create_rectangle(b + px(2), b + px(2), n - b - px(2), n - b - px(2), outline="",
                            fill=COR["poco"] if ativo else COR["desab_poco"])


def partes_do_navio(celulas):
    """{casa: peça} de um navio: proa, meio(s) e popa, na orientação dele."""
    horizontal = len({lin for _, lin in celulas}) == 1
    ordem = sorted(celulas, key=lambda c: c[0] if horizontal else c[1])
    nomes = ("hl", "hm", "hr") if horizontal else ("vt", "vm", "vb")
    ult = len(ordem) - 1
    return {cel: nomes[0] if i == 0 else nomes[2] if i == ult else nomes[1]
            for i, cel in enumerate(ordem)}


# ------------------------------------------------------------------ fontes

def carregar_fontes():
    """Registra as fontes da pasta fontes/ só para este processo (Windows).
    Em outros sistemas, ou sem os arquivos, criar_fontes() usa as do sistema."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        for arq in ("PressStart2P-Regular.ttf", "VT323-Regular.ttf"):
            caminho = os.path.join(PASTA_FONTES, arq)
            if os.path.exists(caminho):
                ctypes.windll.gdi32.AddFontResourceExW(caminho, 0x10, 0)  # FR_PRIVATE
    except Exception:
        pass


def criar_fontes(raiz):
    familias = set(tkfont.families(raiz))
    tem_pixel = "Press Start 2P" in familias
    vt = "VT323" if "VT323" in familias else MONO
    k_vt = 1.0 if vt == "VT323" else 0.7    # a VT323 é estreita; a reserva, não

    def f_pixel(blocos):
        # A Press Start 2P é desenhada numa grade de 8 px: fora dos múltiplos
        # de 8 as letras se deformam (o O vira U). blocos = múltiplos de 8 a P = 4.
        if tem_pixel:
            return tkfont.Font(raiz, family="Press Start 2P", size=-8 * max(1, round(blocos * P / 4)))
        return tkfont.Font(raiz, family=MONO, size=-px(9 * blocos), weight="bold")

    def f_vt(n):
        return tkfont.Font(raiz, family=vt, size=-px(n * k_vt))

    FT.clear()
    FT.update({
        "titulo": f_pixel(3), "h": f_pixel(2), "botao": f_pixel(2), "status": f_pixel(2),
        "tom": f_pixel(2), "pre": f_pixel(2), "rotulo": f_pixel(1), "mini": f_pixel(1),
        "status_p": f_pixel(1),
        "texto": f_vt(22), "texto_p": f_vt(20), "fase": f_vt(24), "canal": f_vt(18),
    })


# ------------------------------------------------------------------ widgets

def painel(master, pad=16):
    """Moldura dos painéis: borda escura, filete claro e o fundo do painel.
    Devolve (de fora, para posicionar; de dentro, para o conteúdo)."""
    fora = tk.Frame(master, bg=COR["sombra"], padx=px(4), pady=px(4))
    meio = tk.Frame(fora, bg=COR["borda"], padx=px(4), pady=px(4))
    meio.pack(fill="both", expand=True)
    dentro = tk.Frame(meio, bg=COR["painel"], padx=px(pad), pady=px(pad))
    dentro.pack(fill="both", expand=True)
    return fora, dentro


def poco(master, padx=12, pady=8):
    """Caixa funda (fundo mais escuro), para leituras: fase, espectro, registro."""
    fora = tk.Frame(master, bg=COR["sombra"], padx=px(4), pady=px(4))
    dentro = tk.Frame(fora, bg=COR["poco"], padx=px(padx), pady=px(pady))
    dentro.pack(fill="both", expand=True)
    return fora, dentro


def rotulo(master, texto, fonte="texto", cor="texto", **kw):
    kw.setdefault("anchor", "w")
    kw.setdefault("justify", "left")
    return tk.Label(master, text=texto, bg=master["bg"], fg=COR[cor], font=FT[fonte], **kw)


def campo(master, var, largura=8):
    return tk.Entry(master, textvariable=var, width=largura, font=FT["texto"], bg=COR["poco"],
                    fg=COR["texto"], insertbackground=COR["texto"], relief="flat", bd=0,
                    highlightthickness=px(4), highlightbackground=COR["sombra"],
                    highlightcolor=COR["ciano"], disabledbackground=COR["desab_poco"],
                    disabledforeground=COR["desab_texto"])


class BotaoPixel(tk.Canvas):
    """Botão com borda escura e relevo, desenhado num Canvas. Aceita
    config(state=..., text=...) como um tk.Button, e primario=True/False."""

    # face, texto, luz, sombra, face com o mouse em cima
    ESTILOS = {
        "primario": (COR["destaque"], COR["fundo"], "#ffe9a0", "#c98a1a", "#ffe27a"),
        "normal": ("#3b3a7a", COR["texto"], "#5856a6", "#26245a", "#4a4994"),
    }

    def __init__(self, master, texto, comando, primario=False, largo=False):
        self.texto, self.comando, self.largo = texto, comando, largo
        self.estilo = "primario" if primario else "normal"
        self.estado = "normal"
        self.sobre = self.apertado = False
        super().__init__(master, width=self._largura(), height=px(44), bg=master["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Configure>", lambda e: self._desenhar())
        self.bind("<Enter>", lambda e: self._sobre(True))
        self.bind("<Leave>", lambda e: self._sobre(False))
        self.bind("<ButtonPress-1>", self._apertar)
        self.bind("<ButtonRelease-1>", self._soltar)

    def _largura(self):
        return FT["botao"].measure(self.texto.upper()) + 2 * px(20)

    def configure(self, cnf=None, **kw):
        kw = {**(cnf or {}), **kw}
        mudou = False
        if "state" in kw:
            estado = kw.pop("state")
            mudou |= estado != self.estado
            self.estado = estado
        if "text" in kw:
            self.texto = kw.pop("text")
            mudou = True
            if not self.largo:
                kw["width"] = self._largura()
        if "primario" in kw:
            self.estilo = "primario" if kw.pop("primario") else "normal"
            mudou = True
        if kw:
            super().configure(**kw)
        if mudou:
            self._desenhar()

    config = configure

    def _sobre(self, dentro):
        self.sobre = dentro
        if not dentro:
            self.apertado = False
        self._desenhar()

    def _apertar(self, ev):
        if self.estado != "disabled":
            self.apertado = True
            self._desenhar()

    def _soltar(self, ev):
        clicou = self.apertado and 0 <= ev.x < self.winfo_width() and 0 <= ev.y < self.winfo_height()
        self.apertado = False
        self._desenhar()
        if clicou and self.estado != "disabled" and self.comando:
            self.comando()

    def _desenhar(self):
        self.delete("all")
        w = self.winfo_width() if self.winfo_width() > 1 else int(self["width"])
        h = int(self["height"])
        dx = 0
        if self.estado == "disabled":
            moldura(self, 0, 0, w, h, COR["desab_fundo"])
            cor = COR["desab_texto"]
        else:
            face, cor, luz, escuro, face_sobre = self.ESTILOS[self.estilo]
            if self.apertado:
                moldura(self, 0, 0, w, h, escuro)
                dx = px(2)
            else:
                moldura(self, 0, 0, w, h, face_sobre if self.sobre else face, luz, escuro)
        texto = self.texto.upper()
        livre = w - 2 * px(12)
        fonte = FT["botao"] if FT["botao"].measure(texto) <= livre else FT["mini"]
        self.create_text(w / 2 + dx, h / 2 + dx, text=texto, fill=cor, font=fonte,
                         width=livre, justify="center")
        tk.Canvas.configure(self, cursor="" if self.estado == "disabled" else "hand2")


class Faixa(tk.Canvas):
    """A faixa de status, com relevo e um ícone conforme o tom."""

    # fundo, texto, luz, sombra, ícone
    ESTILOS = {
        "vez":     (COR["destaque"], COR["fundo"], "#ffe9a0", "#c98a1a", "seta"),
        "ocupado": (COR["borda"], COR["texto"], "#4a47a0", COR["painel"], "som"),
        "espera":  (COR["borda"], COR["texto"], "#4a47a0", COR["painel"], "ouvido"),
        "vitoria": (COR["ok"], COR["fundo"], "#9af0a4", "#2f9a45", "coroa"),
        "derrota": ("#a8233a", COR["texto"], "#d04a5c", "#6e1324", "x"),
        "fraco":   (COR["painel"], COR["fraco"], COR["borda"], COR["apagado"], None),
    }

    def __init__(self, master):
        super().__init__(master, height=px(56), bg=COR["fundo"], highlightthickness=0)
        self.texto, self.tom = "", "fraco"
        self.bind("<Configure>", lambda e: self._desenhar())

    def mostrar(self, texto, tom):
        self.texto, self.tom = texto, tom
        self._desenhar()

    def _desenhar(self):
        self.delete("all")
        w, h = self.winfo_width(), int(self["height"])
        if w < 10:
            return
        fundo, frente, luz, escuro, icone = self.ESTILOS.get(self.tom, self.ESTILOS["ocupado"])
        moldura(self, 0, 0, w, h, fundo, luz, escuro)
        x = px(20)
        if icone:
            q = px(3)
            y = (h - 8 * q) // 2
            for r in ICONES[icone]:
                cor = fundo if len(r) > 4 else frente
                self.create_rectangle(x + r[0] * q, y + r[1] * q, x + (r[0] + r[2]) * q,
                                      y + (r[1] + r[3]) * q, fill=cor, outline="")
            x += 8 * q + px(14)
        texto = self.texto.upper()
        livre = w - x - px(20)
        fonte = FT["status"] if FT["status"].measure(texto) <= livre else FT["status_p"]
        self.create_text(x, h / 2, text=texto, anchor="w", fill=frente, font=fonte, width=livre)


# ----------------------------------------------------------------- partida

class Cancelado(Exception):
    """A partida foi abandonada (nova partida ou janela fechada) no meio de uma jogada."""


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
        self.ultima_resposta = None # o que respondi ao último tiro, para repetir

        self.trava = threading.Lock()
        self.encerrada = threading.Event()
        self.pular = threading.Event()
        self.repetir = threading.Event()

    def checar(self):
        if self.encerrada.is_set():
            raise Cancelado


# ---------------------------------------------------------------- tabuleiro

class Grade(tk.Canvas):
    """Um tabuleiro 6x6 em pixel art. estados: {casa: (peça, efeito ou None)};
    casa sem estado é mar."""

    def __init__(self, master, ao_clicar=None):
        p = P
        self.c, self.g = 12 * p, p              # casa e a linha escura entre as casas
        self.ox, self.oy = 8 * p, 6 * p         # coluna dos números e linha das letras
        self.lado = bn.N * self.c + (bn.N - 1) * self.g
        super().__init__(master, width=self.ox + self.lado + p, height=self.oy + self.lado + p,
                         bg=COR["painel"], highlightthickness=0)
        self.ao_clicar = ao_clicar
        self.ativa = False
        self.estados = {}
        self.ultimo = None
        self.sob_mouse = None
        self.bind("<Motion>", self._mover)
        self.bind("<Leave>", lambda e: self._mover(None))
        self.bind("<Button-1>", self._clique)
        self.desenhar()

    def _xy(self, cel):
        passo = self.c + self.g
        return self.ox + cel[0] * passo, self.oy + cel[1] * passo

    def _cel_em(self, x, y):
        passo = self.c + self.g
        c, l = (x - self.ox) // passo, (y - self.oy) // passo
        if 0 <= c < bn.N and 0 <= l < bn.N:
            return int(c), int(l)
        return None

    def _mover(self, ev):
        cel = self._cel_em(ev.x, ev.y) if ev else None
        if cel != self.sob_mouse:
            antes, self.sob_mouse = self.sob_mouse, cel
            if self.ativa:
                for c in (antes, cel):
                    if c:
                        self._casa(c)

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
        p, passo = P, self.c + self.g
        self.create_rectangle(self.ox - p, self.oy - p, self.ox + self.lado + p,
                              self.oy + self.lado + p, fill=COR["sombra"], outline="")
        for i in range(bn.N):
            meio = i * passo + self.c / 2
            self.create_text(self.ox + meio, 2.5 * p, text=bn.COLS[i], fill=COR["fraco"],
                             font=FT["rotulo"])
            self.create_text(3.5 * p, self.oy + meio, text=str(i + 1), fill=COR["fraco"],
                             font=FT["rotulo"])
        for col in range(bn.N):
            for lin in range(bn.N):
                self._casa((col, lin))

    def _casa(self, cel):
        tag = f"c{cel[0]}_{cel[1]}"
        self.delete(tag)
        x, y = self._xy(cel)
        peca, efeito = self.estados.get(cel, (None, None))
        mira = self.ativa and cel == self.sob_mouse and peca is None
        sprite(self, "agua_mira" if mira else (peca or "agua"), x, y, P, tag)
        for extra in (efeito, "mira" if mira else None, "ultimo" if cel == self.ultimo else None):
            if extra:
                sprite(self, extra, x, y, P, tag)


# ------------------------------------------------------------ nova partida

class DialogoNovaPartida(tk.Toplevel):
    MODOS = [
        ("cpu", "Contra a CPU",
         "As jogadas passam pelo modem em loopback. Não precisa de microfone."),
        ("anfitriao", "Duas máquinas: anfitrião",
         "Áudio real. Você atira primeiro."),
        ("convidado", "Duas máquinas: convidado",
         "Áudio real. O anfitrião atira primeiro."),
    ]

    def __init__(self, master, anterior):
        super().__init__(master, bg=COR["fundo"], padx=px(20), pady=px(20))
        self.title("Nova partida")
        self.resizable(False, False)
        self.resultado = None

        self.modo = tk.StringVar(value=anterior.get("modo", "cpu"))
        self.ouvir = tk.BooleanVar(value=anterior.get("ouvir", False))
        ruido = anterior.get("ruido_db")
        self.ruido = tk.StringVar(value="" if ruido is None else f"{ruido:g}")
        seed = anterior.get("seed")
        self.seed = tk.StringVar(value="" if seed is None else str(seed))

        fora, corpo = painel(self, pad=22)
        fora.pack(fill="both", expand=True)
        rotulo(corpo, "NOVA PARTIDA", "h", "destaque").pack(fill="x")
        rotulo(corpo, "COMO VOCÊ QUER JOGAR?", "botao").pack(fill="x", pady=(px(18), px(12)))

        self.cartoes = []
        for valor, titulo, descricao in self.MODOS:
            borda = tk.Frame(corpo, padx=px(4), pady=px(4), cursor="hand2")
            borda.pack(fill="x", pady=(0, px(10)))
            cartao = tk.Frame(borda, padx=px(14), pady=px(12))
            cartao.pack(fill="x")
            marca = tk.Canvas(cartao, width=px(24), height=px(24), highlightthickness=0)
            marca.pack(side="left", anchor="n", pady=(px(2), 0))
            textos = tk.Frame(cartao)
            textos.pack(side="left", fill="x", expand=True, padx=(px(14), 0))
            t = rotulo(textos, titulo.upper(), "botao")
            t.pack(fill="x")
            d = rotulo(textos, descricao, "texto_p", "fraco", wraplength=px(390))
            d.pack(fill="x", pady=(px(8), 0))
            for w in (borda, cartao, marca, textos, t, d):
                w.bind("<Button-1>", lambda e, v=valor: self._escolher(v))
            self.cartoes.append((valor, borda, (cartao, marca, textos, t, d)))

        borda = tk.Frame(corpo, bg=COR["borda"], padx=px(4), pady=px(4))
        borda.pack(fill="x", pady=(px(8), 0))
        grupo = tk.Frame(borda, bg=COR["painel"], padx=px(14), pady=px(12))
        grupo.pack(fill="x")
        self.lbl_grupo = rotulo(grupo, "SÓ CONTRA A CPU", "mini", "fraco")
        self.lbl_grupo.pack(fill="x", pady=(0, px(12)))
        linha = tk.Frame(grupo, bg=COR["painel"])
        linha.pack(fill="x")
        self.marca_ouvir = tk.Canvas(linha, width=px(24), height=px(24), highlightthickness=0,
                                     bg=COR["painel"])
        self.marca_ouvir.pack(side="left")
        self.lbl_ouvir = rotulo(linha, "Tocar o áudio de cada jogada no alto-falante")
        self.lbl_ouvir.pack(side="left", padx=(px(12), 0))
        for w in (self.marca_ouvir, self.lbl_ouvir):
            w.bind("<Button-1>", lambda e: self._alternar_ouvir())
        linha = tk.Frame(grupo, bg=COR["painel"])
        linha.pack(fill="x", pady=(px(12), 0))
        nomes = tk.Frame(linha, bg=COR["painel"])
        nomes.pack(side="left", fill="x", expand=True)
        self.lbl_ruido = rotulo(nomes, "Ruído no canal (SNR em dB)")
        self.lbl_ruido.pack(fill="x")
        self.lbl_ruido2 = rotulo(nomes, "vazio = sem ruído", "texto_p", "fraco")
        self.lbl_ruido2.pack(fill="x")
        self.ent_ruido = campo(linha, self.ruido)
        self.ent_ruido.pack(side="right", ipady=px(4), ipadx=px(6))

        linha = tk.Frame(corpo, bg=COR["painel"])
        linha.pack(fill="x", pady=(px(16), 0))
        nomes = tk.Frame(linha, bg=COR["painel"])
        nomes.pack(side="left", fill="x", expand=True)
        rotulo(nomes, "Semente").pack(fill="x")
        rotulo(nomes, "opcional, para repetir a mesma partida", "texto_p", "fraco").pack(fill="x")
        campo(linha, self.seed).pack(side="right", ipady=px(4), ipadx=px(6))

        botoes = tk.Frame(corpo, bg=COR["painel"])
        botoes.pack(fill="x", pady=(px(24), 0))
        BotaoPixel(botoes, "Começar ▶", self._ok, primario=True).pack(side="right")
        BotaoPixel(botoes, "Cancelar", self.destroy).pack(side="right", padx=px(12))

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Up>", lambda e: self._mover_modo(-1))
        self.bind("<Down>", lambda e: self._mover_modo(1))
        self._escolher(self.modo.get())
        self.transient(master)
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.focus_set()
        self.wait_window()

    def _escolher(self, valor):
        self.modo.set(valor)
        for v, borda, partes in self.cartoes:
            sel = v == valor
            fundo = COR["borda"] if sel else COR["cartao"]
            borda.config(bg=COR["destaque"] if sel else COR["sombra"])
            for w in partes:
                w.config(bg=fundo)
            partes[3].config(fg=COR["destaque"] if sel else COR["texto"])
            caixa_marcar(partes[1], sel)
        self._ajustar()

    def _mover_modo(self, passo):
        valores = [v for v, _, _ in self.MODOS]
        i = (valores.index(self.modo.get()) + passo) % len(valores)
        self._escolher(valores[i])

    def _alternar_ouvir(self):
        if self.modo.get() == "cpu":
            self.ouvir.set(not self.ouvir.get())
            self._ajustar()

    def _ajustar(self):
        cpu = self.modo.get() == "cpu"
        self.ent_ruido.config(state="normal" if cpu else "disabled")
        caixa_marcar(self.marca_ouvir, self.ouvir.get(), ativo=cpu)
        for w in (self.lbl_ouvir, self.lbl_ruido):
            w.config(fg=COR["texto"] if cpu else COR["desab_texto"])
        for w in (self.lbl_grupo, self.lbl_ruido2):
            w.config(fg=COR["fraco"] if cpu else COR["desab_texto"])
        cursor = "hand2" if cpu else ""
        self.marca_ouvir.config(cursor=cursor)
        self.lbl_ouvir.config(cursor=cursor)

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


# ---------------------------------------------------------------- espectro

N_CANAIS = 17       # o sync e os 16 dígitos
SEGMENTOS = 8


def niveis_tons(bloco):
    """Nível (0 a SEGMENTOS) de cada tom do protocolo num bloco do microfone.

    O nível é quanto o tom se destaca da mediana da faixa, 3 dB por segmento
    a partir de 4 dB: um tom no limiar do receptor (6x, ~16 dB) acende 4.
    Lê só a raia mais próxima de cada tom: com 1024 amostras as raias têm
    ~47 Hz, e um tom forte vaza pouco para os vizinhos (100 Hz ao lado),
    que acendem uns segmentos a menos que ele."""
    bloco = np.asarray(bloco, dtype=np.float64)
    n = len(bloco)
    amp = np.abs(np.fft.rfft(bloco * np.hanning(n)))
    hz = tl.Fs / n
    ref = float(np.median(amp[int(700 / hz):int(3900 / hz) + 1])) or 1.0
    niveis = []
    for f in [tl.fsync] + [tl.fbase + tl.step_hz * d for d in range(16)]:
        db = 20 * np.log10(max(amp[int(round(f / hz))], 1e-9) / ref)
        niveis.append(int(min(SEGMENTOS, max(0, (db - 4) // 3))))
    return niveis


def tom_tocando(t, hexstr):
    """(canal, índice do dígito) do tom que toca t segundos depois do início
    da transmissão de hexstr; canal 0 é o sync. Mesma temporização de
    tl.hex_to_signal."""
    t -= tl.WARMUP
    if 0 <= t < tl.syncDur:
        return 0, None
    t -= tl.syncDur + tl.MIDGAP
    passo = tl.dur + tl.gap
    i = int(t // passo) if t >= 0 else -1
    if 0 <= i < len(hexstr) and t - i * passo < tl.dur:
        return 1 + int(hexstr[i], 16), i
    return None, None


# ------------------------------------------------------------------ janela

class App(tk.Tk):
    def __init__(self):
        global P
        super().__init__()
        self.title(f"Batalha Naval Sonora ({VERSAO})")
        self.configure(bg=COR["fundo"])

        self.p = None
        self.opcoes = {"modo": "cpu"}
        self.fila = queue.Queue()
        self.anim = None            # (início, duração, hex) da transmissão em curso
        self.ouvindo_desde = None
        self.varrendo = False       # barra em vaivém: ouvindo ou decodificando
        self.texto_fase = ""
        self.repetir_modo = None    # None, "tiro" ou "resposta"
        self.ultimo_tom = 0.0       # quando o microfone reconheceu um tom do protocolo
        self.dica_dada = False

        # A arte tem 4 px por pixel a 100% de escala; numa tela que não comporta
        # a janela, diminui até caber.
        escala = max(1.0, self.winfo_fpixels("1i") / 96)
        P = max(2, round(4 * escala))
        livre_a = self.winfo_screenheight() - px(40) - 60
        livre_l = self.winfo_screenwidth() - 40
        while True:
            criar_fontes(self)
            self._estilos()
            self._montar()
            self.update_idletasks()
            if P <= 2 or (self.winfo_reqheight() <= livre_a and self.winfo_reqwidth() <= livre_l):
                break
            self.raiz.destroy()
            P -= 1
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())

        self._fase_parado()
        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self.after(40, self._bombear)
        self.after(100, self._tique)
        self.after(150, self.nova_partida)

    # ---- montagem

    def _estilos(self):
        estilo = ttk.Style(self)
        estilo.theme_use("clam")
        estilo.configure("Log.Vertical.TScrollbar", troughcolor=COR["poco"],
                         background=COR["borda"], bordercolor=COR["poco"],
                         arrowcolor=COR["fraco"], lightcolor=COR["borda"],
                         darkcolor=COR["borda"])

    def _montar(self):
        self.raiz = raiz = tk.Frame(self, bg=COR["fundo"], padx=px(20), pady=px(20))
        raiz.pack(fill="both", expand=True)

        topo = tk.Frame(raiz, bg=COR["fundo"])
        topo.pack(fill="x")
        logo = tk.Canvas(topo, width=12 * P, height=12 * P, bg=COR["fundo"], highlightthickness=0)
        logo.pack(side="left")
        sprite(logo, "logo", 0, 0, P)
        titulo = "BATALHA NAVAL"
        cv = tk.Canvas(topo, width=FT["titulo"].measure(titulo) + px(6), height=px(48),
                       bg=COR["fundo"], highlightthickness=0)
        cv.pack(side="left", padx=(px(18), 0))
        cv.create_text(px(4), px(4), text=titulo, anchor="nw", fill=COR["sombra"], font=FT["titulo"])
        cv.create_text(0, 0, text=titulo, anchor="nw", fill=COR["destaque"], font=FT["titulo"])
        cv.create_text(0, px(38), text="S O N O R A", anchor="nw", fill=COR["ciano"],
                       font=FT["rotulo"])
        chip = tk.Frame(topo, bg=COR["borda"], padx=px(4), pady=px(4))
        chip.pack(side="left", padx=(px(18), 0))
        self.lbl_modo = tk.Label(chip, text="", bg=COR["poco"], fg=COR["fraco"], font=FT["texto"],
                                 padx=px(10), pady=px(2))
        self.lbl_modo.pack()
        self.btn_nova = BotaoPixel(topo, "Nova partida", self.nova_partida, primario=True)
        self.btn_nova.pack(side="right")
        self.btn_sortear = BotaoPixel(topo, "Sortear navios", self.sortear)
        self.btn_sortear.pack(side="right", padx=px(12))

        self.faixa = Faixa(raiz)
        self.faixa.pack(fill="x", pady=px(12))

        meio = tk.Frame(raiz, bg=COR["fundo"])
        meio.pack(fill="both", expand=True)
        esquerda = tk.Frame(meio, bg=COR["fundo"])
        esquerda.pack(side="left", fill="y")
        tabuleiros = tk.Frame(esquerda, bg=COR["fundo"])
        tabuleiros.pack()
        self.grade_meu, self.cv_frota = self._coluna(tabuleiros, "SUA FROTA", None)
        self.grade_mira, self.cv_placar = self._coluna(tabuleiros, "MAR INIMIGO", self._atirar)
        self._legenda(esquerda)
        self._painel_registro(esquerda)
        self._painel_modem(meio)

    def _coluna(self, master, titulo, ao_clicar):
        fora, col = painel(master)
        fora.pack(side="left", anchor="n", padx=(0 if ao_clicar is None else px(20), 0))
        rotulo(col, titulo, "h", "destaque").pack(fill="x", pady=(0, px(10)))
        grade = Grade(col, ao_clicar)
        grade.pack()
        info = tk.Canvas(col, width=int(grade["width"]), height=3 * px(26) + 2 * px(6),
                         bg=COR["painel"], highlightthickness=0)
        info.pack(pady=(px(12), 0))
        return grade, info

    def _legenda(self, master):
        q = max(1, P // 2)
        itens = [("hm", "navio"), ("boom", "acerto"), ("wreck", "afundado"), ("splash", "água"),
                 ("ultimo", "último tiro")]
        larg = sum(12 * q + px(8) + FT["texto"].measure(t) + px(22) for _, t in itens)
        alt = max(12 * q, FT["texto"].metrics("linespace"))
        cv = tk.Canvas(master, width=larg, height=alt, bg=COR["fundo"], highlightthickness=0)
        cv.pack(anchor="w", pady=(px(14), 0), padx=(px(4), 0))
        x, y = 0, (alt - 12 * q) // 2
        for peca, texto in itens:
            if peca == "ultimo":
                sprite(cv, "agua", x, y, q)
            sprite(cv, peca, x, y, q)
            x += 12 * q + px(8)
            cv.create_text(x, alt / 2, text=texto, anchor="w", fill=COR["fraco"], font=FT["texto"])
            x += FT["texto"].measure(texto) + px(22)

    def _painel_modem(self, master):
        fora, modem = painel(master)
        fora.pack(side="left", fill="both", expand=True, padx=(px(20), 0))

        linha = tk.Frame(modem, bg=COR["painel"])
        linha.pack(fill="x")
        self.luz = tk.Canvas(linha, width=px(16), height=px(16), bg=COR["painel"],
                             highlightthickness=0)
        self.luz.pack(side="left")
        rotulo(linha, "MODEM", "h", "destaque").pack(side="left", padx=(px(10), 0))
        rotulo(linha, "16-FSK · 10 bit/s", cor="fraco").pack(side="right")

        bw, bgap, sh, sg = px(13), px(3), px(8), px(2)
        larg_esp = N_CANAIS * bw + (N_CANAIS - 1) * bgap

        caixa_f, caixa = poco(modem)
        caixa_f.pack(fill="x", pady=(px(14), 0))
        self.lbl_pre = rotulo(caixa, "··", "pre", "fraco", width=4)
        self.lbl_pre.pack(side="left", anchor="n", pady=(px(5), 0))
        self.lbl_fase = rotulo(caixa, "parado", "fase", anchor="nw", height=2,
                               wraplength=larg_esp - FT["pre"].measure("TX ▶") - px(10))
        self.lbl_fase.pack(side="left", fill="x", expand=True, padx=(px(10), 0))

        rotulo(modem, "TONS · SYNC 1 kHz · DADOS 2–3,5 kHz", cor="fraco").pack(
            fill="x", pady=(px(14), px(6)))
        esp_f, esp = poco(modem, padx=8, pady=8)
        esp_f.pack(anchor="w")
        alt_rot = FT["canal"].metrics("linespace")
        self.cv_espectro = cv = tk.Canvas(esp, width=larg_esp,
                                          height=SEGMENTOS * (sh + sg) + px(2) + alt_rot,
                                          bg=COR["poco"], highlightthickness=0)
        cv.pack()
        self.seg_espectro, self.rot_espectro = [], []
        for j in range(N_CANAIS):
            x = j * (bw + bgap)
            self.seg_espectro.append([
                cv.create_rectangle(x, (SEGMENTOS - 1 - s) * (sh + sg), x + bw,
                                    (SEGMENTOS - 1 - s) * (sh + sg) + sh,
                                    fill=COR["apagado"], outline="")
                for s in range(SEGMENTOS)])
            self.rot_espectro.append(cv.create_text(
                x + bw / 2, SEGMENTOS * (sh + sg) + px(2), anchor="n", font=FT["canal"],
                text="S" if j == 0 else "0123456789ABCDEF"[j - 1],
                fill=COR["fogo"] if j == 0 else COR["fraco"]))

        linha = tk.Frame(modem, bg=COR["painel"])
        linha.pack(fill="x", pady=(px(14), 0))
        rotulo(linha, "TOM", cor="fraco").pack(side="left")
        self.lbl_hz = rotulo(linha, "----", "tom", "ok")
        self.lbl_hz.pack(side="left", padx=(px(12), 0))
        self.lbl_dig = rotulo(linha, "", "tom")
        self.lbl_dig.pack(side="left", padx=(px(12), 0))

        rotulo(modem, "TRANSMISSÃO", cor="fraco").pack(fill="x", pady=(px(14), px(6)))
        self.barra, self.blocos_barra = self._segmentos(modem, 20, px(12))
        rotulo(modem, "MICROFONE", cor="fraco").pack(fill="x", pady=(px(14), px(6)))
        self.medidor, self.blocos_mic = self._segmentos(modem, 12, px(20))

        tk.Frame(modem, bg=COR["painel"]).pack(fill="both", expand=True)
        self.btn_repetir = BotaoPixel(modem, "Repetir tiro", self._repetir, largo=True)
        self.btn_repetir.config(state="disabled")
        self.btn_repetir.pack(fill="x", pady=(px(14), 0))
        self.btn_parar = BotaoPixel(modem, "Parar de ouvir", self._parar_de_ouvir, largo=True)
        self.btn_parar.config(state="disabled")
        self.btn_parar.pack(fill="x", pady=(px(10), 0))

    def _segmentos(self, master, n, largura):
        cv = tk.Canvas(master, width=n * largura + (n - 1) * px(2), height=px(16),
                       bg=COR["painel"], highlightthickness=0)
        cv.pack(anchor="w")
        ids = [cv.create_rectangle(i * (largura + px(2)), 0, i * (largura + px(2)) + largura,
                                   px(16), fill=COR["apagado"], outline="") for i in range(n)]
        return cv, ids

    def _painel_registro(self, master):
        fora, reg = painel(master)
        fora.pack(fill="both", expand=True, pady=(px(14), 0))
        rotulo(reg, "REGISTRO", "h", "destaque").pack(fill="x", pady=(0, px(10)))
        quadro_f, quadro = poco(reg, padx=0, pady=0)
        quadro_f.pack(fill="both", expand=True)
        # width=1: a largura vem da coluna dos tabuleiros, não do texto
        self.log = tk.Text(quadro, width=1, height=4, bg=COR["poco"], fg=COR["texto"], font=FT["texto_p"],
                           relief="flat", padx=px(12), pady=px(6), wrap="word", state="disabled",
                           highlightthickness=0, bd=0)
        rolagem = ttk.Scrollbar(quadro, command=self.log.yview, style="Log.Vertical.TScrollbar")
        self.log.config(yscrollcommand=rolagem.set)
        rolagem.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, cor in (("tx", COR["destaque"]), ("rx", COR["ciano"]), ("jogo", COR["texto"]),
                         ("erro", COR["alerta"]), ("aviso", COR["alerta"]),
                         ("hora", COR["fraco"]), ("fim", COR["ok"])):
            self.log.tag_config(tag, foreground=cor)

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
        p.ultima_resposta = None
        self.grade_mira.atualizar(self.grade_mira.estados, cel, ativa=False)
        self.btn_sortear.config(state="disabled")
        self._modo_repetir(None)
        alvo = self._rodada_cpu if p.modo == "cpu" else self._rodada_audio
        self._em_thread(p, alvo, cel)

    def _parar_de_ouvir(self):
        if self.p:
            self.p.pular.set()

    def _repetir(self):
        p = self.p
        if not p or p.fim:
            return
        if self.repetir_modo == "tiro":
            self.btn_repetir.config(state="disabled")
            p.repetir.set()
        elif self.repetir_modo == "resposta" and self.grade_mira.ativa and p.ultima_resposta:
            self.grade_mira.atualizar(self.grade_mira.estados, self.grade_mira.ultimo, ativa=False)
            self._modo_repetir(None)
            self._em_thread(p, self._repetir_resposta)

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
        self._ui(p, self._status, f"O inimigo atira em {bn.fmt_coord(cel)}...", "espera")
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
        venceu, tiro_dele = self._meu_turno_audio(p, alvo)
        if venceu:
            return self._ui(p, self._terminar, True)
        self._turno_dele_e_seguir(p, tiro_dele)

    def _turno_dele_e_seguir(self, p, tiro_dele=None):
        if self._turno_dele_audio(p, tiro_dele):
            return self._ui(p, self._terminar, False)
        self._ui(p, self._sua_vez_agora)

    def _meu_turno_audio(self, p, alvo):
        """Atira e espera a resposta, repetindo o tiro quando pedido.

        Devolve (venceu, tiro_dele). tiro_dele é preenchido quando, em vez da
        resposta, chega um tiro: o adversário ouviu o nosso, respondeu, a
        resposta se perdeu e ele já está na vez dele."""
        coord = bn.fmt_coord(alvo)
        tentativa = 1
        self._ui(p, self._status, f"Transmitindo {coord}... silêncio na sala", "ocupado")
        self._enviar(p, coord)
        while True:
            extra = f" (tentativa {tentativa})" if tentativa > 1 else ""
            self._ui(p, self._status, f"Aguardando a resposta ao tiro em {coord}{extra}...", "espera")
            resposta = self._ouvir(p, "a resposta", repetir=f"Repetir tiro {coord}")

            if resposta is REPETIR:
                tentativa += 1
                self._ui(p, self._log, f"Repetindo o tiro em {coord} (tentativa {tentativa}).", "jogo")
                self._ui(p, self._status, f"Transmitindo {coord} de novo... silêncio na sala", "ocupado")
                self._enviar(p, coord)
                continue
            if resposta is None:
                self._ui(p, self._log, f"Você desistiu de esperar a resposta ao tiro em {coord}. "
                                       "Jogada perdida.", "aviso")
                return False, None
            if resposta in RESPOSTAS:
                break
            tiro_dele = bn.parse_coord(resposta)
            if tiro_dele is not None:
                with p.trava:
                    p.ultimo_mira = None
                self._ui(p, self._redesenhar)
                self._ui(p, self._log, f"Chegou um tiro em vez da resposta: o adversário ouviu o seu "
                                       f"tiro em {coord}, mas a resposta se perdeu. Você pode atirar "
                                       "lá de novo depois.", "aviso")
                return False, tiro_dele
            self._ui(p, self._log, f"Resposta ilegível (chegou {resposta!r}). Continuo ouvindo: repita "
                                   "o tiro ou peça ao adversário para repetir a resposta.", "aviso")

        with p.trava:
            p.mira[alvo] = resposta
            p.ultimo_mira = alvo
        self._ui(p, self._redesenhar)
        self._ui(p, self._log, f"Seu tiro em {coord}: {bn.descrever(resposta)}", "jogo")
        return resposta == bn.VITORIA, None

    def _turno_dele_audio(self, p, cel=None):
        """Recebe um tiro e responde. Um tiro ilegível não encerra a espera:
        o adversário pode repeti-lo. cel: tiro que já chegou (veja _meu_turno_audio)."""
        while cel is None:
            self._ui(p, self._status, "Aguardando o tiro do adversário...", "espera")
            texto = self._ouvir(p, "o tiro do adversário")
            if texto is None:
                self._ui(p, self._log, "Você desistiu de esperar o tiro. A vez passa para você.", "aviso")
                return False
            cel = bn.parse_coord(texto)
            if cel is not None:
                break
            if texto in RESPOSTAS:
                self._ui(p, self._log, f"Chegou uma resposta repetida ({texto!r}), não um tiro. "
                                       "Continuo esperando o tiro.", "aviso")
            else:
                self._ui(p, self._log, f"Não entendi o tiro (chegou {texto!r}). Continuo ouvindo: "
                                       "o adversário pode repetir o tiro.", "aviso")

        with p.trava:
            p.iniciou = True
            resultado = p.meu.receber_tiro(cel)
            p.ultimo_meu = cel
            p.ultima_resposta = resultado
        self._ui(p, self._redesenhar)
        self._ui(p, self._log, f"O adversário atirou em {bn.fmt_coord(cel)}: {bn.descrever(resultado)}", "jogo")
        self._ui(p, self._status, "Respondendo...", "ocupado")
        self._enviar(p, resultado)
        return resultado == bn.VITORIA

    def _repetir_resposta(self, p):
        resposta = p.ultima_resposta
        self._ui(p, self._log, f"Repetindo a resposta {resposta!r} ({bn.descrever(resposta)}).", "jogo")
        self._ui(p, self._status, "Repetindo a resposta... silêncio na sala", "ocupado")
        self._enviar(p, resposta)
        self._ui(p, self._sua_vez_agora)

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
        self._ui(p, self._log, f"{quem:>7} > {texto!r:<5} hex {_hex(texto):<6} {dur:4.1f}s  {marca}",
                 "tx" if recebido == texto else "erro")
        self._ui(p, self._fase_parado)
        return recebido

    def _enviar(self, p, texto):
        sinal = tl.text_to_signal(texto)
        dur = len(sinal) / tl.Fs
        self._ui(p, self._fase_tx, texto, dur)
        tl.tocar(sinal)
        p.checar()
        self._ui(p, self._log, f"enviado  > {texto!r:<5} hex {_hex(texto):<6} {dur:4.1f}s", "tx")
        self._ui(p, self._fase_parado)

    def _ouvir(self, p, oque, repetir=None):
        """Como tl.gravar(), mas com saída: Parar de ouvir, Repetir tiro ou uma
        nova partida encerram a escuta. Devolve o texto decodificado, None se
        interrompida por Parar de ouvir, ou REPETIR.

        repetir: rótulo do botão Repetir enquanto ouve; None o deixa desligado."""
        sd = tl._sounddevice()
        p.pular.clear()
        p.repetir.clear()
        self._ui(p, self._fase_rx, oque, repetir)
        analisar = getattr(tl, "analisar_bloco", None)
        ultimo = [0.0]

        def blocos():
            while not (p.pular.is_set() or p.repetir.is_set() or p.encerrada.is_set()):
                dados, _overflow = stream.read(tl.CHUNK)
                bloco = dados[:, 0]
                agora = time.monotonic()
                if agora - ultimo[0] >= 0.08:
                    ultimo[0] = agora
                    rms = float(np.sqrt(np.mean(bloco.astype(np.float64) ** 2)))
                    canal = None
                    if analisar:
                        tem_tom, eh_sync, freq = analisar(bloco)
                        digito = round((freq - tl.fbase) / tl.step_hz)
                        if eh_sync:
                            canal = 0
                        elif tem_tom and 0 <= digito < 16:
                            canal = 1 + digito
                    self._ui(p, self._nivel, rms, canal, niveis_tons(bloco))
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
        if p.repetir.is_set():
            p.repetir.clear()
            return REPETIR
        if p.pular.is_set():
            p.pular.clear()
            return None

        texto = tl.signal_to_text(sinal)
        motivo = info.get("motivo", "")
        self._ui(p, self._log, f"recebido < {texto!r:<5} hex {_hex(texto):<6} "
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
                for cel, parte in partes_do_navio(navio["celulas"]).items():
                    if afundado:
                        meu[cel] = ("wreck", None)
                    else:
                        meu[cel] = (parte, "fogo" if cel in navio["atingidas"] else None)
                n, t = len(navio["atingidas"]), len(navio["celulas"])
                pips = [COR["acerto"]] * n + [COR["casco"]] * (t - n)
                if afundado:
                    frota.append((navio["nome"].upper(), pips, "AFUNDADO", COR["alerta"]))
                elif n:
                    frota.append((navio["nome"].upper(), pips, f"ATINGIDO {n}/{t}", COR["fogo"]))
                else:
                    frota.append((navio["nome"].upper(), pips, "INTACTO", COR["fraco"]))
            for cel in p.meu.tiros_recebidos:
                meu.setdefault(cel, ("splash", None))

            peca = {bn.AGUA: "splash", bn.ACERTO: "boom", bn.AFUNDOU: "wreck", bn.VITORIA: "wreck"}
            mira = {cel: (peca[r], None) for cel, r in p.mira.items()}
            if p.fim and p.dele:
                for cel in p.dele.ocupadas():
                    mira.setdefault(cel, ("oculto", None))
            afundados = sum(1 for r in p.mira.values() if r in (bn.AFUNDOU, bn.VITORIA))
            acertos = sum(1 for r in p.mira.values() if r != bn.AGUA)
            tiros = len(p.mira)

        self.grade_meu.atualizar(meu, p.ultimo_meu)
        self.grade_mira.atualizar(mira, p.ultimo_mira)
        self._info(self.cv_frota, frota)
        self._info(self.cv_placar, [
            ("NAVIOS AFUNDADOS", None, f"{afundados} DE {len(bn.NAVIOS)}", COR["texto"]),
            ("TIROS", None, str(tiros), COR["texto"]),
            ("ACERTOS", None, str(acertos), COR["fogo"] if acertos else COR["texto"])])
        self.btn_sortear.config(state="disabled" if p.iniciou or p.fim else "normal")

    def _info(self, cv, linhas):
        """Linhas embaixo de um tabuleiro: nome, quadradinhos de vida e situação."""
        cv.delete("all")
        w, h, f = int(cv["width"]), px(26), FT["texto"]
        for i, (nome, pips, valor, cor) in enumerate(linhas):
            y = i * (h + px(6)) + h / 2
            cv.create_text(0, y, text=nome, anchor="w", font=f,
                           fill=COR["texto"] if pips is not None else COR["fraco"])
            for j, cor_pip in enumerate(pips or ()):
                x = px(112) + j * px(18)
                cv.create_rectangle(x, y - px(8), x + px(16), y + px(8), fill=COR["sombra"], outline="")
                cv.create_rectangle(x + px(2), y - px(6), x + px(14), y + px(6), fill=cor_pip,
                                    outline="")
            cv.create_text(w, y, text=valor, anchor="e", fill=cor, font=f)

    def _sua_vez_agora(self):
        p = self.p
        self._status("Sua vez! Clique numa casa do mar inimigo", "vez")
        self._redesenhar()
        self.grade_mira.atualizar(self.grade_mira.estados, self.grade_mira.ultimo, ativa=True)
        if p.modo != "cpu" and p.ultima_resposta:
            self._modo_repetir("resposta", f"Repetir resposta ({CURTA[p.ultima_resposta]})")

    def _modo_repetir(self, modo, rotulo=None):
        self.repetir_modo = modo
        self.dica_dada = False
        if rotulo:
            self.btn_repetir.config(text=rotulo)
        self.btn_repetir.config(state="normal" if modo else "disabled", primario=bool(modo))

    def _terminar(self, venceu):
        p = self.p
        p.fim = "vitoria" if venceu else "derrota"
        self._modo_repetir(None)
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
        self._modo_repetir(None)
        self._log(f"Erro: {e}", "erro")
        self._status("Erro no áudio. Veja o registro e comece uma nova partida.", "derrota")
        self.grade_mira.atualizar(self.grade_mira.estados, self.grade_mira.ultimo, ativa=False)
        messagebox.showerror("Batalha Naval Sonora", str(e), parent=self)

    def _status(self, texto, tom="ocupado"):
        self.faixa.mostrar(texto, tom)

    def _log(self, texto, tag="jogo"):
        self.log.config(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S  "), "hora")
        self.log.insert("end", texto + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    # ---- painel do modem

    def _fase(self, pre, cor_pre, texto, luz):
        self.lbl_pre.config(text=pre, fg=COR[cor_pre])
        self.texto_fase = texto
        self.luz.delete("all")
        self.luz.create_rectangle(0, 0, px(16), px(16), fill=COR["sombra"], outline="")
        self.luz.create_rectangle(px(2), px(2), px(14), px(14), fill=COR[luz], outline="")

    def _fase_tx(self, texto, dur):
        self.anim = (time.monotonic(), dur, _hex(texto))
        self.ouvindo_desde = None
        self.varrendo = False
        self._fase("TX ▶", "destaque", f"{texto} · hex {_hex(texto)} · {dur:.1f} s", "destaque")
        self.btn_parar.config(state="disabled")
        self._modo_repetir(None)

    def _fase_rx(self, oque, repetir=None):
        self.anim = None
        self.ouvindo_desde = time.monotonic()
        self.varrendo = True
        self._fase("RX ◀", "ciano", f"ouvindo {oque}", "ciano")
        self.btn_parar.config(state="normal")
        self._modo_repetir("tiro" if repetir else None, repetir)

    def _fase_ocupado(self, texto):
        self.anim = None
        self.ouvindo_desde = None
        self.varrendo = True
        self._fase("··", "fraco", texto, "ok")
        self.btn_parar.config(state="disabled")

    def _fase_parado(self):
        self.anim = None
        self.ouvindo_desde = None
        self.varrendo = False
        self._fase("··", "fraco", "parado", "fumaca")
        self.btn_parar.config(state="disabled")
        self._modo_repetir(None)
        self._barra()
        self._nivel(0.0, None, [0] * N_CANAIS)

    def _nivel(self, rms, canal, niveis=None):
        db = 20 * np.log10(max(rms, 1.0) / 32768)          # dBFS, de -90 a 0
        frac = min(1.0, max(0.0, (db + 80) / 80))
        acesos = round(len(self.blocos_mic) * frac)
        for i, bloco in enumerate(self.blocos_mic):
            cor = ("apagado" if i >= acesos else "ok" if i < 8 else "brasa" if i < 10 else "acerto")
            self.medidor.itemconfig(bloco, fill=COR[cor])
        if niveis is not None:
            self._espectro(niveis, canal)
        self._mostrar_tom(canal)
        if canal is not None:
            self.ultimo_tom = time.monotonic()

    def _espectro(self, niveis, ativo=None):
        for j, nivel in enumerate(niveis):
            for s, seg in enumerate(self.seg_espectro[j]):
                if s >= nivel:
                    cor = "apagado"
                elif j == ativo:
                    cor = "destaque"
                else:
                    cor = "ciano" if s >= 5 else "onda"
                self.cv_espectro.itemconfig(seg, fill=COR[cor])
            self.cv_espectro.itemconfig(self.rot_espectro[j], fill=COR[
                "destaque" if j == ativo else "fogo" if j == 0 else "fraco"])

    def _mostrar_tom(self, canal):
        if canal is None:
            hz, dig = "----", ""
        elif canal == 0:
            hz, dig = f"{tl.fsync:.0f} Hz", "sync"
        else:
            hz, dig = f"{tl.fbase + tl.step_hz * (canal - 1):.0f} Hz", f"→ {'0123456789abcdef'[canal - 1]}"
        self.lbl_hz.config(text=hz)
        self.lbl_dig.config(text=dig)

    def _barra(self, frac=None, varredura=None):
        n = len(self.blocos_barra)
        cheios = round(n * frac) if frac is not None else 0
        if varredura is not None:
            pos = int(varredura * 12) % (2 * (n - 3))
            ini = pos if pos <= n - 3 else 2 * (n - 3) - pos
        for i, bloco in enumerate(self.blocos_barra):
            if i < cheios:
                cor = "destaque"
            elif varredura is not None and ini <= i < ini + 3:
                cor = "ciano"
            else:
                cor = "apagado"
            self.barra.itemconfig(bloco, fill=COR[cor])

    def _tique(self):
        agora = time.monotonic()
        texto = self.texto_fase
        if self.anim:
            ini, dur, hx = self.anim
            self._barra(min(1.0, (agora - ini) / dur))
            canal, i = tom_tocando(agora - ini, hx)
            niveis = [0] * N_CANAIS
            if canal is not None:
                niveis[canal] = SEGMENTOS
            self._espectro(niveis, canal)
            self._mostrar_tom(canal)
            if i is not None:
                texto += f" · dígito {i + 1}/{len(hx)}"
        elif self.varrendo:
            self._barra(varredura=agora)
        if self.ouvindo_desde:
            ouvindo = agora - self.ouvindo_desde
            texto += f" · {ouvindo:.0f} s"
            if self.repetir_modo == "tiro":
                # Repetir por cima de uma resposta que está chegando estragaria as duas.
                chegando = agora - self.ultimo_tom < TOM_RECENTE_S
                if chegando:
                    texto += " · recebendo sinal"
                estado = "disabled" if chegando else "normal"
                if estado != self.btn_repetir.estado:
                    self.btn_repetir.config(state=estado, primario=not chegando)
                if ouvindo > DICA_SEM_RESPOSTA_S and not chegando and not self.dica_dada:
                    self.dica_dada = True
                    self._status(f"Sem resposta há {DICA_SEM_RESPOSTA_S}s. Se o adversário não ouviu "
                                 "o tiro, clique em Repetir tiro.", "vez")
        if self.lbl_fase.cget("text") != texto:
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
    carregar_fontes()
    try:
        App().mainloop()
    except KeyboardInterrupt:  # Ctrl+C no terminal que abriu a janela
        print("\n  Encerrado.")


if __name__ == "__main__":
    main()

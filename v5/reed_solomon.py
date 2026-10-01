"""
Reed-Solomon sobre GF(256), so com a biblioteca padrao.

Corrige erros (simbolos errados em posicao desconhecida) e apagamentos
(simbolos errados em posicao CONHECIDA). Com `nsym` bytes de paridade, corrige
qualquer combinacao com  2*erros + apagamentos <= nsym.  Um apagamento custa
metade de um erro, e e por isso que o receptor da v5 marca como apagamento os
tons que ouviu com pouca certeza, em vez de apostar num valor.

Polinomios sao listas com o coeficiente de MENOR grau primeiro, exceto onde
dito. Uma palavra-codigo e uma lista de bytes cujo indice 0 e o coeficiente de
maior grau; o byte na posicao p tem grau n-1-p.
"""

_PRIM = 0x11d  # x^8 + x^4 + x^3 + x^2 + 1

_EXP = [0] * 512
_LOG = [0] * 256


def _tabelas():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= _PRIM
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_tabelas()


class ErroRS(Exception):
    """Mais danos do que a paridade consegue reparar."""


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _div(a, b):
    if a == 0:
        return 0
    return _EXP[_LOG[a] - _LOG[b] + 255]


def _poly_mul(p, q):
    r = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        if a:
            for j, b in enumerate(q):
                r[i + j] ^= _mul(a, b)
    return r


def _poly_eval(p, x):
    """p em ordem de menor grau primeiro."""
    y = 0
    for c in reversed(p):
        y = _mul(y, x) ^ c
    return y


def _gerador(nsym):
    """g(x) = (x - a^0)(x - a^1)...(x - a^(nsym-1)), maior grau primeiro."""
    g = [1]
    for i in range(nsym):
        novo = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            novo[j] ^= c
            novo[j + 1] ^= _mul(c, _EXP[i])
        g = novo
    return g


_GERADORES = {}


def codificar(msg, nsym):
    """msg (bytes) -> msg + nsym bytes de paridade (lista de ints)."""
    msg = list(msg)
    if len(msg) + nsym > 255:
        raise ValueError(f"palavra-codigo de {len(msg) + nsym} bytes passa de 255")
    if nsym not in _GERADORES:
        _GERADORES[nsym] = _gerador(nsym)
    g = _GERADORES[nsym]
    saida = msg + [0] * nsym
    for i in range(len(msg)):
        c = saida[i]
        if c:
            for j in range(1, len(g)):
                saida[i + j] ^= _mul(g[j], c)
    saida[:len(msg)] = msg
    return saida


def _sindromes(cw, nsym):
    n = len(cw)
    s = []
    for j in range(nsym):
        x = _EXP[j]
        y = 0
        for c in cw:
            y = _mul(y, x) ^ c
        s.append(y)
    return s


def _berlekamp_massey(s):
    """Localizador de erros C(x) (menor grau primeiro) a partir de uma
    sequencia de sindromes. Devolve (C, L)."""
    c, b, l, m, bb = [1], [1], 0, 1, 1
    for n in range(len(s)):
        d = s[n]
        for i in range(1, l + 1):
            if i < len(c):
                d ^= _mul(c[i], s[n - i])
        if d == 0:
            m += 1
            continue
        coef = _div(d, bb)
        termo = [0] * m + [_mul(coef, v) for v in b]
        if 2 * l <= n:
            t = c[:]
            c = [(c[i] if i < len(c) else 0) ^ (termo[i] if i < len(termo) else 0)
                 for i in range(max(len(c), len(termo)))]
            l, b, bb, m = n + 1 - l, t, d, 1
        else:
            c = [(c[i] if i < len(c) else 0) ^ (termo[i] if i < len(termo) else 0)
                 for i in range(max(len(c), len(termo)))]
            m += 1
    return c, l


def decodificar(cw, nsym, apagamentos=()):
    """Corrige a palavra-codigo e devolve (mensagem, n_corrigidos).

    apagamentos: posicoes (indices em cw) que o chamador sabe estarem
    duvidosas. Levanta ErroRS se o dano passar da capacidade.
    """
    cw = list(cw)
    n = len(cw)
    apagamentos = sorted(set(apagamentos))
    f = len(apagamentos)
    if f > nsym:
        raise ErroRS("apagamentos demais")

    s = _sindromes(cw, nsym)
    if not any(s):
        return cw[:n - nsym], 0

    # localizador dos apagamentos: produto de (1 + X_e * x)
    gama = [1]
    for p in apagamentos:
        gama = _poly_mul(gama, [1, _EXP[n - 1 - p]])

    # sindromes de Forney: tiram a contribuicao dos apagamentos, sobrando so
    # a dos erros desconhecidos
    t = []
    for i in range(f, nsym):
        v = 0
        for j in range(f + 1):
            v ^= _mul(gama[j], s[i - j])
        t.append(v)
    loc_erros, l = _berlekamp_massey(t)
    if 2 * l > nsym - f:
        raise ErroRS("erros demais")

    psi = _poly_mul(loc_erros, gama)
    while len(psi) > 1 and psi[-1] == 0:
        psi.pop()
    grau = len(psi) - 1

    # Chien: as raizes de psi sao os inversos das posicoes erradas
    pos = []
    for p in range(n):
        if _poly_eval(psi, _EXP[(255 - (n - 1 - p)) % 255]) == 0:
            pos.append(p)
    if len(pos) != grau:
        raise ErroRS("localizador inconsistente")

    # Forney: valor do erro em cada posicao
    omega = _poly_mul(s, psi)[:nsym]
    dpsi = [psi[i] if i % 2 == 1 else 0 for i in range(1, len(psi))]
    for p in pos:
        x = _EXP[n - 1 - p]
        xi = _EXP[(255 - (n - 1 - p)) % 255]
        den = _poly_eval(dpsi, xi)
        if den == 0:
            raise ErroRS("derivada nula")
        cw[p] ^= _mul(x, _div(_poly_eval(omega, xi), den))

    if any(_sindromes(cw, nsym)):
        raise ErroRS("nao convergiu")
    return cw[:n - nsym], len(pos)

/*
 * Regras da Batalha Naval: porta de v4/batalha_naval.py.
 *
 * Mesmo tabuleiro (6x6), mesma frota e o mesmo protocolo de mensagens, para
 * que o navegador jogue contra o jogo em Python:
 *
 *   tiro      "B3"   coordenada, 2 bytes
 *   resposta  "A"    água
 *             "X"    acerto
 *             "D"    navio afundado
 *             "V"    último navio afundado, quem atirou venceu
 *
 * Uma casa é guardada como um número: linha * N + coluna.
 */

export const COLS = 'ABCDEF';
export const N = 6;
export const NAVIOS = [['Cruzador', 3], ['Fragata', 2], ['Patrulha', 2]];
export const AGUA = 'A', ACERTO = 'X', AFUNDOU = 'D', VITORIA = 'V';
export const RESPOSTAS = [AGUA, ACERTO, AFUNDOU, VITORIA];

export const casa = (c, l) => l * N + c;
export const coluna = (id) => id % N;
export const linha = (id) => Math.floor(id / N);

/** parse_coord: "b3" -> casa, ou null se não for uma coordenada válida. */
export function lerCoord(s) {
  s = (s || '').trim().toUpperCase().replace(/ /g, '');
  if (s.length !== 2 || !COLS.includes(s[0]) || !/[0-9]/.test(s[1])) return null;
  const l = Number(s[1]);
  if (l < 1 || l > N) return null;
  return casa(COLS.indexOf(s[0]), l - 1);
}

export const fmtCoord = (id) => `${COLS[coluna(id)]}${linha(id) + 1}`;

export function descrever(resultado) {
  return { [AGUA]: 'água', [ACERTO]: 'acerto!', [AFUNDOU]: 'afundou um navio!',
           [VITORIA]: 'afundou o último navio!' }[resultado] || 'resposta ininteligível';
}

const sortear = (rng, n) => Math.floor(rng() * n);

/** O tabuleiro de um jogador: onde estão os navios dele e o que já levou tiro. */
export class Tabuleiro {
  constructor(rng = Math.random) {
    this.navios = [];
    this.tirosRecebidos = new Set();
    for (const [nome, tamanho] of NAVIOS) this._posicionar(nome, tamanho, rng);
  }

  _posicionar(nome, tamanho, rng) {
    const ocupadas = this.ocupadas();
    for (;;) {
      const celulas = new Set();
      if (rng() < 0.5) {
        const c = sortear(rng, N - tamanho + 1), l = sortear(rng, N);
        for (let i = 0; i < tamanho; i++) celulas.add(casa(c + i, l));
      } else {
        const c = sortear(rng, N), l = sortear(rng, N - tamanho + 1);
        for (let i = 0; i < tamanho; i++) celulas.add(casa(c, l + i));
      }
      if ([...celulas].every((id) => !ocupadas.has(id))) {
        this.navios.push({ nome, celulas, atingidas: new Set() });
        return;
      }
    }
  }

  ocupadas() {
    return new Set(this.navios.flatMap((n) => [...n.celulas]));
  }

  receberTiro(id) {
    this.tirosRecebidos.add(id);
    for (const navio of this.navios) {
      if (navio.celulas.has(id)) {
        navio.atingidas.add(id);
        if (afundado(navio)) return this.derrotado() ? VITORIA : AFUNDOU;
        return ACERTO;
      }
    }
    return AGUA;
  }

  derrotado() {
    return this.navios.every(afundado);
  }
}

export const afundado = (navio) => navio.atingidas.size === navio.celulas.size;

/** Atira ao acaso até acertar; depois insiste nas casas vizinhas. */
export class CPU {
  constructor(rng = Math.random) {
    this.rng = rng;
    this.tentadas = new Set();
    this.pendentes = [];
  }

  escolher() {
    while (this.pendentes.length) {
      const id = this.pendentes.shift();
      if (!this.tentadas.has(id)) {
        this.tentadas.add(id);
        return id;
      }
    }
    const livres = [];
    for (let id = 0; id < N * N; id++) if (!this.tentadas.has(id)) livres.push(id);
    const id = livres[sortear(this.rng, livres.length)];
    this.tentadas.add(id);
    return id;
  }

  informar(id, resultado) {
    if (resultado !== ACERTO) return;
    const c = coluna(id), l = linha(id);
    for (const [vc, vl] of [[c + 1, l], [c - 1, l], [c, l + 1], [c, l - 1]]) {
      if (vc >= 0 && vc < N && vl >= 0 && vl < N && !this.tentadas.has(casa(vc, vl))) {
        this.pendentes.push(casa(vc, vl));
      }
    }
  }
}

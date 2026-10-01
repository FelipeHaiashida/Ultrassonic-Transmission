/*
 * Modem da v4 em JavaScript: porta fiel do v4/transfer_lib.py.
 *
 * O objetivo é conversar com o jogo em Python: um tiro tocado pelo navegador
 * tem que ser ouvido pelo transfer_lib, e vice-versa. Por isso cada função
 * aqui repete a de lá passo a passo, com os mesmos parâmetros, as mesmas
 * janelas e as mesmas expressões (inclusive int() e round() do Python, que
 * viram pyInt e pyRound). Mudou o transfer_lib, muda aqui também.
 *
 * Duas diferenças de implementação, sem efeito no resultado:
 *  - os espectros saem do algoritmo de Goertzel, só nas raias necessárias, em
 *    vez de uma FFT inteira (as janelas de leitura têm 7200 amostras, que não
 *    é potência de 2);
 *  - o passa-faixa de find_data_start aplica, no domínio da frequência, a
 *    resposta de butter(6) passada por sosfiltfilt (|H|², fase zero).
 *
 * A taxa de amostragem é um parâmetro (Fs): o navegador usa a da placa de
 * som, 48000 ou 44100 Hz. Tudo no protocolo é medido em segundos e em Hz,
 * então os dois lados não precisam usar a mesma taxa.
 *
 * Não depende de página nenhuma: roda no navegador e no Node (teste_modem.mjs).
 */

export const PARAMS = Object.freeze({
  dur: 0.25,          // duração de cada tom de dado
  gap: 0.15,          // silêncio entre tons
  fsync: 1000,        // frequência do sincronismo
  fbase: 2000,        // frequência do dígito hex 0
  step_hz: 100,       // distância entre dígitos (0 -> 2000 Hz ... f -> 3500 Hz)
  syncDur: 1.5,       // perfil 'padrao', o que o jogo em Python usa
  WARMUP: 2.5,
  MIDGAP: 0.5,
  COOLDOWN: 1.0,
  FADE_IN: 0.5,
  FIM_SILENCIO: 2.5,
  AMPLITUDE: 30,
  CHUNK: 1024,
  MAX_RECORD_S: 30,
  PROEMINENCIA: 6.0,
  TOM_MINIMO: 30,     // em unidades de int16, como no Python
  SYNC_MIN_S: 0.25,
  PRE_ROLL_S: 0.3,
  JANELA_RUIDO_HZ: 800,
  NIVEL_SAIDA: 18000, // pico do sinal normalizado (normalize_audio)
});
const P = PARAMS;

export const pyInt = Math.trunc;

/** round() do Python: empate vai para o par. */
export function pyRound(x) {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 ? 2 * Math.round(x / 2) : r;
}

// ------------------------------------------------------------------ janelas

const _janelas = new Map();
function janela(tipo, n) {
  const chave = tipo + n;
  let w = _janelas.get(chave);
  if (!w) {
    w = new Float64Array(n);
    if (n === 1) w[0] = 1;
    for (let k = 0; n > 1 && k < n; k++) {
      const a = 2 * Math.PI * k / (n - 1);
      w[k] = tipo === 'blackman' ? 0.42 - 0.5 * Math.cos(a) + 0.08 * Math.cos(2 * a)
                                 : 0.5 - 0.5 * Math.cos(a);   // np.hanning
    }
    let soma = 0;
    for (let k = 0; k < n; k++) soma += w[k];
    w.soma = soma;
    _janelas.set(chave, w);
  }
  return w;
}

// -------------------------------------------------------------- codificação

/** hex_to_signal: string hexadecimal -> áudio (amplitude AMPLITUDE, sem normalizar). */
export function hexParaSinal(hex, Fs) {
  const nTom = pyInt(P.dur * Fs), nGap = pyInt(P.gap * Fs);
  const nWarm = pyInt(P.WARMUP * Fs), nSync = pyInt(P.syncDur * Fs);
  const nMid = pyInt(P.MIDGAP * Fs), nCool = pyInt(P.COOLDOWN * Fs);
  const s = new Float64Array(nWarm + nSync + nMid + hex.length * (nTom + nGap) + nCool);

  // _make_sync + apply_super_slow_fade
  let o = nWarm;
  const passoSync = P.syncDur / nSync;
  for (let i = 0; i < nSync; i++) s[o + i] = P.AMPLITUDE * Math.sin(2 * Math.PI * P.fsync * (i * passoSync));
  const nFade = Math.min(pyInt(P.FADE_IN * Fs), pyInt(nSync * 0.33));
  for (let i = 0; i < nFade; i++) {
    const t = nFade > 1 ? -Math.PI / 2 + i * (Math.PI / 2) / (nFade - 1) : -Math.PI / 2;
    s[o + i] *= Math.sin(t) + 1;
  }
  const nSaida = pyInt(nSync * 0.1);
  for (let i = 0; i < nSaida; i++) s[o + nSync - nSaida + i] *= nSaida > 1 ? 1 - i / (nSaida - 1) : 1;

  // dados
  o += nSync + nMid;
  const w = janela('blackman', nTom), passoTom = P.dur / nTom;
  for (const ch of hex) {
    const f = P.fbase + P.step_hz * parseInt(ch, 16);
    for (let i = 0; i < nTom; i++) s[o + i] = P.AMPLITUDE * Math.sin(2 * Math.PI * f * (i * passoTom)) * w[i];
    o += nTom + nGap;
  }
  return s;
}

const _utf8 = new TextEncoder();
export function textoParaHex(texto) {
  return Array.from(_utf8.encode(texto), (b) => b.toString(16).padStart(2, '0')).join('');
}

export function textoParaSinal(texto, Fs) {
  return hexParaSinal(textoParaHex(texto), Fs);
}

/** normalize_audio: pico em NIVEL_SAIDA, truncado como o astype(int16). */
export function normalizar(sinal) {
  let m = 0;
  for (let i = 0; i < sinal.length; i++) m = Math.max(m, Math.abs(sinal[i]));
  const out = new Float64Array(sinal.length);
  if (m === 0) return out;
  for (let i = 0; i < sinal.length; i++) out[i] = pyInt((sinal[i] / m) * P.NIVEL_SAIDA);
  return out;
}

// --------------------------------------------------------- análise espectral

/** |rfft(seg * w)| nas raias [a0, a1), pelo algoritmo de Goertzel. */
function espectro(seg, w, a0, a1) {
  const n = seg.length, x = new Float64Array(n);
  for (let i = 0; i < n; i++) x[i] = seg[i] * w[i];
  const out = new Float64Array(a1 - a0);
  for (let k = a0; k < a1; k++) {
    const c = 2 * Math.cos(2 * Math.PI * k / n);
    let s1 = 0, s2 = 0;
    for (let i = 0; i < n; i++) {
      const s0 = x[i] + c * s1 - s2;
      s2 = s1;
      s1 = s0;
    }
    out[k - a0] = Math.sqrt(Math.max(0, s1 * s1 + s2 * s2 - c * s1 * s2));
  }
  return out;
}

function mediana(vals) {
  vals.sort();
  return vals[vals.length >> 1];
}

/**
 * _proeminencia: (raia, amplitude, proeminência) do pico mais destacado da
 * faixa [k0, k1) do espectro de seg (janela w). Proeminência = amplitude /
 * mediana dos vizinhos (median_filter com mode='nearest').
 */
function picoDestacado(seg, w, Fs, k0, k1) {
  const n = seg.length, nAmp = (n >> 1) + 1;
  const lado = Math.max(8, pyInt(P.JANELA_RUIDO_HZ / 2 * n / Fs));
  const a0 = Math.max(0, k0 - lado), a1 = Math.min(nAmp, k1 + lado);
  const amp = espectro(seg, w, a0, a1);
  const escala = 2 / w.soma;
  for (let i = 0; i < amp.length; i++) amp[i] *= escala;

  const m = amp.length, viz = new Float64Array(2 * lado + 1);
  let melhor = -1, prom = -1, pico = 0;
  for (let k = k0; k < k1; k++) {
    const j = k - a0;
    for (let d = -lado; d <= lado; d++) viz[d + lado] = amp[Math.min(m - 1, Math.max(0, j + d))];
    const ref = mediana(viz), a = amp[j];
    const p = ref > 0 ? a / ref : (a > 0 ? Infinity : 0);
    if (p > prom) { prom = p; melhor = k; pico = a; }
  }
  return { k: melhor, pico, prom };
}

/** analisar_bloco: classifica um bloco do microfone (valores em escala int16). */
export function analisarBloco(bloco, Fs) {
  const n = bloco.length;
  const k0 = Math.ceil(Math.max(50, P.fsync - 300) * n / Fs);
  const k1 = Math.min((n >> 1) + 1, pyInt((P.fbase + 16 * P.step_hz + 300) * n / Fs) + 1);
  const { k, pico, prom } = picoDestacado(bloco, janela('hanning', n), Fs, k0, k1);
  const freq = k * Fs / n;
  const temTom = pico >= P.TOM_MINIMO && prom >= P.PROEMINENCIA;
  const ehSync = temTom && Math.abs(freq - P.fsync) <= 2 * Fs / n;
  return { temTom, ehSync, freq, pico };
}

/** _tom_na_faixa: o pico mais destacado dentro da faixa dos dígitos. */
function tomNaFaixa(seg, Fs) {
  const n = seg.length;
  const k0 = Math.ceil((P.fbase - P.step_hz) * n / Fs);
  const k1 = pyInt((P.fbase + 16 * P.step_hz) * n / Fs) + 1;
  const { k, pico, prom } = picoDestacado(seg, janela('blackman', n), Fs, k0, k1);
  return { freq: k * Fs / n, pico, prom };
}

// -------------------------------------------------------------- alinhamento

function fft(re, im, inv) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (inv ? 2 : -2) * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len >> 1; k++) {
        const a = i + k, b = a + (len >> 1);
        const tr = re[b] * cr - im[b] * ci, ti = re[b] * ci + im[b] * cr;
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr; im[a] += ti;
        const t = cr * wr - ci * wi;
        ci = cr * wi + ci * wr;
        cr = t;
      }
    }
  }
  if (inv) for (let i = 0; i < n; i++) { re[i] /= n; im[i] /= n; }
}

/**
 * _filtrar: butter(6, [lo, hi], 'bandpass') com sosfiltfilt. A ida e a volta
 * do filtfilt dão fase zero e magnitude |H|² = 1 / (1 + x^12), com x a
 * frequência analógica (pré-distorcida pela bilinear) levada ao protótipo
 * passa-baixa.
 */
function filtrarFaixa(x, lo, hi, Fs) {
  const folga = Math.ceil(0.25 * Fs);
  let n = 1;
  while (n < x.length + 2 * folga) n <<= 1;
  const re = new Float64Array(n), im = new Float64Array(n);
  re.set(x, folga);
  fft(re, im, false);
  const wa = (f) => 2 * Fs * Math.tan(Math.PI * f / Fs);
  const wl = wa(lo), wh = wa(hi), w0q = wl * wh, bw = wh - wl;
  for (let k = 0; k < n; k++) {
    const f = (k <= n >> 1 ? k : n - k) * Fs / n;
    let g = 0;
    if (f > 0 && f < Fs / 2) {
      const w = wa(f), r = (w * w - w0q) / (w * bw);
      g = 1 / (1 + r ** 12);
    }
    re[k] *= g;
    im[k] *= g;
  }
  fft(re, im, true);
  return re.subarray(folga, folga + x.length);
}

function envelope(x, win = 256) {
  const n = Math.floor(x.length / win), env = new Float64Array(n);
  for (let b = 0; b < n; b++) {
    let m = 0;
    for (let i = b * win; i < (b + 1) * win; i++) m = Math.max(m, Math.abs(x[i]));
    env[b] = m;
  }
  return env;
}

/** np.percentile com interpolação linear. */
function percentil(vals, q) {
  const s = Float64Array.from(vals).sort();
  if (!s.length) return 0;
  const pos = (s.length - 1) * q / 100, i = Math.floor(pos), f = pos - i;
  return i + 1 < s.length ? s[i] + f * (s[i + 1] - s[i]) : s[i];
}

function maximo(a, de = 0) {
  let m = -Infinity;
  for (let i = de; i < a.length; i++) if (a[i] > m) m = a[i];
  return m;
}

/** find_data_start: índice do primeiro tom de dado, ou null. */
export function inicioDosDados(sinal, Fs) {
  if (sinal.length < P.CHUNK || !sinal.some((v) => v !== 0)) return null;
  const win = 256;
  const envS = envelope(filtrarFaixa(sinal, P.fsync - 60, P.fsync + 60, Fs), win);
  const envD = envelope(filtrarFaixa(sinal, P.fbase - 150, P.fbase + 15 * P.step_hz + 150, Fs), win);
  const n = envS.length;

  // 1. o sync: o primeiro trecho alto por pelo menos 30% de syncDur
  const ruidoS = percentil(envS, 10), picoS = maximo(envS);
  if (picoS < Math.max(P.TOM_MINIMO, 4 * ruidoS)) return null;
  const limS = ruidoS + 0.3 * (picoS - ruidoS);
  const minimo = Math.max(1, pyInt(0.3 * P.syncDur * Fs / win));
  let i = 0, fimSync = null;
  while (i < n) {
    if (!(envS[i] >= limS)) { i++; continue; }
    let j = i;
    while (j < n && envS[j] >= limS) j++;
    if (j - i >= minimo) { fimSync = j; break; }
    i = j;
  }
  if (fimSync === null || fimSync >= n) return null;
  i = fimSync;

  // 2. o primeiro tom de dado depois do sync
  const ruidoD = percentil(envD, 10), picoD = maximo(envD, fimSync);
  if (picoD < Math.max(P.TOM_MINIMO, 4 * ruidoD)) return null;
  const alto = ruidoD + 0.10 * (picoD - ruidoD), baixo = ruidoD + 0.02 * (picoD - ruidoD);
  while (i < n && envD[i] < alto) i++;
  if (i >= n) return null;
  while (i > fimSync && envD[i - 1] >= baixo) i--;

  // 3. refina pela grade inteira de tons
  const passoW = pyRound((P.dur + P.gap) * Fs / win);
  const tomW = pyRound(P.dur * Fs / win);
  const previsto = fimSync + pyRound(P.MIDGAP * Fs / win);
  const meio = Math.floor(passoW / 2);
  const centro = Math.abs(i - previsto) <= meio ? i : previsto;
  const lo = Math.max(fimSync, centro - meio), hi = centro + meio;
  const tons = Math.floor((n - hi - tomW) / passoW) + 1;
  if (tons >= 1 && hi > lo) {
    const acum = new Float64Array(n + 1);
    for (let k = 0; k < n; k++) acum[k + 1] = acum[k] + envD[k] * envD[k];
    let melhor = -Infinity, arg = 0;
    for (let s = lo; s < hi; s++) {
      let e = 0;
      for (let t = 0; t < tons; t++) {
        const a = s + passoW * t;
        e += acum[a + tomW] - acum[a];
      }
      if (e > melhor) { melhor = e; arg = s - lo; }
    }
    i = lo + arg;
  }
  return i * win;
}

// ------------------------------------------------------------ decodificação

/** signal_to_hex */
export function sinalParaHex(sinal, Fs) {
  const inicio = inicioDosDados(sinal, Fs);
  if (inicio === null) return '';
  const passo = pyInt((P.dur + P.gap) * Fs);
  const recuo = pyInt(0.20 * P.dur * Fs);
  const largura = pyInt(0.60 * P.dur * Fs);

  const leituras = [];
  const total = Math.floor((sinal.length - inicio - recuo - largura) / passo) + 1;
  for (let k = 0; k < total; k++) {
    const a = inicio + k * passo + recuo;
    leituras.push(tomNaFaixa(sinal.subarray(a, a + largura), Fs));
  }
  const fortes = leituras.filter((l) => l.prom >= P.PROEMINENCIA).map((l) => l.pico);
  const corte = fortes.length ? Math.max(P.TOM_MINIMO, 0.03 * Math.max(...fortes)) : Infinity;

  let hex = '';
  for (const { freq, pico, prom } of leituras) {
    if (prom < P.PROEMINENCIA || pico < corte) continue;
    const val = pyRound((freq - P.fbase) / P.step_hz);
    if (val >= 0 && val <= 15) hex += val.toString(16);
  }
  return hex;
}

const _deUtf8 = new TextDecoder('utf-8');
/** signal_to_text, com errors='ignore' */
export function sinalParaTexto(sinal, Fs) {
  let hex = sinalParaHex(sinal, Fs);
  if (hex.length % 2) hex = hex.slice(0, -1);
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.substr(2 * i, 2), 16);
  return _deUtf8.decode(bytes).replace(/�/g, '');
}

// ------------------------------------------------------------------- canal

/**
 * _coletar, em forma de fluxo: recebe os blocos do microfone um a um (push),
 * espera o sync se sustentar por SYNC_MIN_S e grava até FIM_SILENCIO sem tom
 * do protocolo. Quando terminou fica true, sinal() devolve a gravação.
 */
export class Receptor {
  constructor(Fs, maxS = P.MAX_RECORD_S) {
    const C = P.CHUNK;
    this.Fs = Fs;
    this.maxS = maxS;
    this.nSync = Math.max(1, pyRound(Math.min(P.SYNC_MIN_S, 0.4 * P.syncDur) * Fs / C));
    this.nHistorico = this.nSync + pyInt(P.PRE_ROLL_S * Fs / C);
    this.limiteSilencio = (Fs / C) * P.FIM_SILENCIO;
    this.historico = [];
    this.coletado = [];
    this.gravando = false;
    this.terminou = false;
    this.silencio = 0;
    this.seguidos = 0;
    this.total = 0;
    this.motivo = 'fim do audio';
  }

  push(bloco) {
    const info = analisarBloco(bloco, this.Fs);
    if (this.terminou) return info;
    if (!this.gravando) {
      this.historico.push(bloco);
      if (this.historico.length > this.nHistorico) this.historico.shift();
      this.seguidos = info.ehSync ? this.seguidos + 1 : 0;
      if (this.seguidos >= this.nSync) {
        this.gravando = true;
        this.coletado = this.historico.slice();
        this.total = this.coletado.reduce((s, b) => s + b.length, 0);
      }
      return info;
    }
    this.coletado.push(bloco);
    this.total += bloco.length;
    if (this.total > this.maxS * this.Fs) {
      this.motivo = `teto de ${this.maxS}s`;
      this.terminou = true;
      return info;
    }
    this.silencio = info.temTom ? 0 : this.silencio + 1;
    if (this.silencio > this.limiteSilencio) {
      this.motivo = 'silencio final';
      this.terminou = true;
    }
    return info;
  }

  sinal() {
    const out = new Float64Array(this.coletado.reduce((s, b) => s + b.length, 0));
    let o = 0;
    for (const b of this.coletado) { out.set(b, o); o += b.length; }
    return out;
  }
}

/** Ruído gaussiano de média 0 a partir de um gerador uniforme em [0, 1). */
function gaussiano(rng) {
  let u = 0;
  while (u === 0) u = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rng());
}

/** adicionar_ruido: SNR medida contra a amplitude do tom, não contra o RMS do arquivo. */
export function adicionarRuido(sinal, snrDb, rng = Math.random) {
  const tomRms = P.NIVEL_SAIDA / Math.SQRT2;
  const ruidoRms = tomRms / 10 ** (snrDb / 20);
  const out = new Float64Array(sinal.length);
  for (let i = 0; i < sinal.length; i++) {
    out[i] = pyInt(Math.min(32767, Math.max(-32768, sinal[i] + ruidoRms * gaussiano(rng))));
  }
  return out;
}

/**
 * loopback: passa o sinal pelo receptor inteiro sem tocar em hardware, com
 * ambienteS de silêncio no fim (é esse silêncio que encerra a recepção).
 * Devolve o texto decodificado.
 */
export function loopback(sinal, Fs, { ruidoDb = null, rng = Math.random, ambienteS = 3.0 } = {}) {
  const norm = normalizar(sinal);
  let s = new Float64Array(norm.length + pyInt(ambienteS * Fs));
  s.set(norm);
  if (ruidoDb !== null) s = adicionarRuido(s, ruidoDb, rng);
  const rec = new Receptor(Fs);
  for (let i = 0; i + P.CHUNK <= s.length && !rec.terminou; i += P.CHUNK) rec.push(s.subarray(i, i + P.CHUNK));
  return sinalParaTexto(rec.sinal(), Fs);
}

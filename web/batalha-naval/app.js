/*
 * Batalha Naval Sonora no navegador.
 *
 * Mesmo fluxo de v4/batalha_naval_gui.py: as jogadas, o "Repetir tiro", o
 * "Repetir resposta" e a recuperação quando uma mensagem se perde no ar. O
 * modem (modem.js) é o da v4, então esta página joga contra outra cópia dela
 * (PC ou celular) e contra o jogo em Python.
 */

import * as M from './modem.js';
import * as J from './jogo.js';

const $ = (s) => document.querySelector(s);
const REPETIR = Symbol('repetir');
const CANCELADO = Symbol('cancelado');
const DICA_SEM_RESPOSTA_S = 15;   // tiro + fim da recepção do outro lado + resposta ≈ 12 s
const TOM_RECENTE_S = 3;          // tom do protocolo ouvido há menos disso: algo está chegando
const FS_LOOPBACK = 48000;        // contra o computador sem áudio, não há placa de som envolvida

class Cancelado extends Error {}
const pausa = (ms) => new Promise((r) => setTimeout(r, ms));

// =================================================================== áudio

/* Coleta o microfone em blocos de CHUNK amostras, como o stream.read() do Python. */
const CODIGO_COLETOR = `
const CHUNK = ${M.PARAMS.CHUNK};
class Coletor extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(CHUNK); this.n = 0; }
  process(entradas) {
    const ch = entradas[0] && entradas[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === CHUNK) {
        this.port.postMessage(this.buf, [this.buf.buffer]);   // depois disto, this.buf fica vazio
        this.buf = new Float32Array(CHUNK);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor('coletor', Coletor);`;

const audio = {
  ctx: null,
  Fs: FS_LOOPBACK,
  stream: null,
  ouvinte: null,     // função que recebe cada bloco do microfone, ou null
  fonte: null,       // o som tocando agora

  /** Tem que ser chamada dentro de um clique: navegadores só liberam áudio assim. */
  async preparar(comMic) {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    const retomar = this.ctx.resume();
    if (comMic && !this.stream) {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        throw new Error('O navegador só libera o microfone em página HTTPS (ou em localhost). ' +
                        'Abra o jogo pelo endereço https.');
      }
      try {
        // Sem cancelamento de eco, supressão de ruído nem ganho automático:
        // os três tratam os tons do protocolo como ruído e os apagam.
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
        });
      } catch (e) {
        throw new Error(e.name === 'NotAllowedError'
          ? 'Sem permissão para usar o microfone. Libere o microfone para esta página nas configurações do navegador e tente de novo.'
          : `Não consegui abrir o microfone (${e.message || e.name}).`);
      }
      await retomar;
      const fonte = this.ctx.createMediaStreamSource(this.stream);
      const mudo = this.ctx.createGain();
      mudo.gain.value = 0;
      mudo.connect(this.ctx.destination);
      const entregar = (f32) => this.ouvinte && this.ouvinte(f32);
      if (this.ctx.audioWorklet) {
        const url = URL.createObjectURL(new Blob([CODIGO_COLETOR], { type: 'application/javascript' }));
        await this.ctx.audioWorklet.addModule(url);
        const no = new AudioWorkletNode(this.ctx, 'coletor');
        no.port.onmessage = (e) => entregar(e.data);
        fonte.connect(no);
        no.connect(mudo);
      } else {
        const no = this.ctx.createScriptProcessor(M.PARAMS.CHUNK, 1, 1);
        no.onaudioprocess = (e) => entregar(new Float32Array(e.inputBuffer.getChannelData(0)));
        fonte.connect(no);
        no.connect(mudo);
      }
    }
    await retomar;
    this.Fs = this.ctx.sampleRate;
    if (M.PARAMS.fbase + 16 * M.PARAMS.step_hz >= this.Fs / 2) {
      throw new Error(`Este aparelho trabalha a ${this.Fs} Hz e não consegue tocar os tons do protocolo.`);
    }
  },

  /** Toca um sinal já normalizado (escala int16). Resolve quando termina. */
  async tocar(norm, ganho = 1) {
    if (this.ctx.state !== 'running') await this.ctx.resume();
    const buf = this.ctx.createBuffer(1, norm.length, this.Fs), ch = buf.getChannelData(0);
    for (let i = 0; i < norm.length; i++) ch[i] = (norm[i] / 32768) * ganho;
    return new Promise((resolve) => {
      const s = this.ctx.createBufferSource();
      s.buffer = buf;
      s.connect(this.ctx.destination);
      s.onended = () => { if (this.fonte === s) this.fonte = null; resolve(); };
      this.fonte = s;
      s.start();
    });
  },

  parar() {
    this.ouvinte = null;
    if (this.fonte) { try { this.fonte.stop(); } catch { /* já parou */ } }
  },
};

let travaTela = null;
async function manterTelaLigada(ligar) {
  try {
    if (ligar && 'wakeLock' in navigator && !travaTela) {
      travaTela = await navigator.wakeLock.request('screen');
      travaTela.addEventListener('release', () => { travaTela = null; });
    } else if (!ligar && travaTela) {
      await travaTela.release();
    }
  } catch { /* opcional: nem todo navegador tem */ }
}

// ================================================================= partida

class Partida {
  constructor(modo, { ouvir = false, ruidoDb = null, volMax = false } = {}) {
    this.modo = modo;
    this.ouvir = ouvir;
    this.ruidoDb = ruidoDb;
    this.ganho = volMax ? 32767 / M.PARAMS.NIVEL_SAIDA : 1;
    this.meu = new J.Tabuleiro();
    this.dele = modo === 'cpu' ? new J.Tabuleiro() : null;
    this.cpu = modo === 'cpu' ? new J.CPU() : null;
    this.mira = new Map();          // casa -> resposta recebida
    this.ultimoMeu = null;
    this.ultimoMira = null;
    this.iniciou = false;           // depois do primeiro tiro, os navios não mudam mais
    this.fim = null;                // 'vitoria' / 'derrota'
    this.ultimaResposta = null;     // o que respondi ao último tiro, para repetir
    this.encerrada = false;
    this.espera = null;             // encerra a escuta em curso: espera(valor)
  }

  checar() {
    if (this.encerrada) throw new Cancelado();
  }
}

let p = null;   // a partida atual

const ui = {
  vez: false,             // tabuleiro do inimigo aceita tiro
  repetirModo: null,      // null, 'tiro' ou 'resposta'
  anim: null,             // { inicio, dur } da transmissão em curso
  ouvindoDesde: null,
  textoFase: 'parado',
  ultimoTom: 0,
  dicaDada: false,
  ocupado: false,
};

function rodar(fn, ...args) {
  const partida = p;
  fn(partida, ...args).catch((e) => {
    if (e instanceof Cancelado || partida.encerrada) return;
    console.error(e);
    erro(e);
  });
}

function encerrar() {
  if (!p) return;
  p.encerrada = true;
  if (p.espera) p.espera(CANCELADO);
  audio.parar();
}

// =================================================================== canal

async function modemLoopback(partida, texto, quem) {
  const Fs = partida.ouvir ? audio.Fs : FS_LOOPBACK;
  const sinal = M.textoParaSinal(texto, Fs);
  const dur = sinal.length / Fs;
  if (partida.ouvir) {
    faseTx(texto, dur);
    await audio.tocar(M.normalizar(sinal), partida.ganho);
  } else {
    faseOcupado(`modulando e decodificando '${texto}'`);
  }
  await pausa(30);                        // deixa a tela atualizar antes da conta
  partida.checar();
  const recebido = M.loopback(sinal, Fs, { ruidoDb: partida.ruidoDb });
  partida.checar();
  const ok = recebido === texto;
  log(`${quem.padStart(7)} → '${texto}'`.padEnd(17) + ` hex ${M.textoParaHex(texto).padEnd(6)} ${dur.toFixed(1)}s  ` +
      (ok ? 'ok' : `ERRO, chegou '${recebido}'`), ok ? 'tx' : 'erro');
  faseParado();
  return recebido;
}

async function enviar(partida, texto) {
  const sinal = M.textoParaSinal(texto, audio.Fs);
  const dur = sinal.length / audio.Fs;
  faseTx(texto, dur);
  await audio.tocar(M.normalizar(sinal), partida.ganho);
  partida.checar();
  log(`enviado  → '${texto}'`.padEnd(17) + ` hex ${M.textoParaHex(texto).padEnd(6)} ${dur.toFixed(1)}s`, 'tx');
  faseParado();
}

/**
 * Escuta o microfone até receber uma mensagem completa. Devolve o texto,
 * null (Parar de ouvir) ou REPETIR (Repetir tiro).
 */
function ouvir(partida, oque, repetirRotulo = null) {
  return new Promise((resolve, reject) => {
    const rec = new M.Receptor(audio.Fs);
    const inicio = performance.now();
    let ultimoNivel = 0, decodificando = false;

    const fim = (valor) => {
      if (partida.espera !== fim) return;
      partida.espera = null;
      audio.ouvinte = null;
      if (valor === CANCELADO) return reject(new Cancelado());
      faseParado();
      resolve(valor);
    };
    partida.espera = fim;
    faseRx(oque, repetirRotulo);

    audio.ouvinte = (f32) => {
      if (decodificando) return;
      const bloco = new Float64Array(f32.length);
      for (let i = 0; i < f32.length; i++) bloco[i] = f32[i] * 32768;
      const info = rec.push(bloco);
      const agora = performance.now();
      if (info.temTom) ui.ultimoTom = agora;
      if (agora - ultimoNivel >= 80) {
        ultimoNivel = agora;
        let soma = 0;
        for (let i = 0; i < bloco.length; i++) soma += bloco[i] * bloco[i];
        nivel(Math.sqrt(soma / bloco.length), info.ehSync ? 'SYNC' : info.temTom ? `${info.freq.toFixed(0)}Hz` : '');
      }
      if (!rec.terminou) return;
      decodificando = true;
      audio.ouvinte = null;
      ui.textoFase = 'decodificando';
      setTimeout(() => {
        if (partida.espera !== fim) return;
        const texto = M.sinalParaTexto(rec.sinal(), audio.Fs);
        log(`recebido ← '${texto}'`.padEnd(17) + ` hex ${M.textoParaHex(texto).padEnd(6)} ` +
            `${((performance.now() - inicio) / 1000).toFixed(1)}s ouvindo  (${rec.motivo})`, 'rx');
        fim(texto);
      }, 20);
    };
  });
}

// ================================================================= jogadas

async function rodadaCpu(partida, alvo) {
  const coord = J.fmtCoord(alvo);
  status(`Transmitindo o seu tiro em ${coord}...`);
  const alvoRec = J.lerCoord(await modemLoopback(partida, coord, 'você'));
  if (alvoRec === null) {
    log('O inimigo não entendeu o tiro. Jogada perdida.', 'aviso');
    return suaVezAgora();
  }
  const resultado = partida.dele.receberTiro(alvoRec);
  status('O inimigo está respondendo...');
  const resposta = await modemLoopback(partida, resultado, 'inimigo');
  if (!J.RESPOSTAS.includes(resposta)) {
    log('A resposta chegou corrompida. Você não soube o resultado.', 'aviso');
    return suaVezAgora();
  }
  partida.mira.set(alvoRec, resposta);
  partida.ultimoMira = alvoRec;
  desenhar();
  log(`Seu tiro em ${J.fmtCoord(alvoRec)}: ${J.descrever(resposta)}`);
  if (resposta === J.VITORIA) return terminar(true);

  const cel = partida.cpu.escolher();
  status(`O computador atira em ${J.fmtCoord(cel)}...`);
  const celRec = J.lerCoord(await modemLoopback(partida, J.fmtCoord(cel), 'inimigo'));
  if (celRec === null) {
    log('Você não entendeu o tiro do computador. Jogada perdida.', 'aviso');
    return suaVezAgora();
  }
  const meuResultado = partida.meu.receberTiro(celRec);
  partida.ultimoMeu = celRec;
  desenhar();
  status('Você está respondendo...');
  await modemLoopback(partida, meuResultado, 'você');
  partida.cpu.informar(celRec, meuResultado);
  log(`Computador em ${J.fmtCoord(celRec)}: ${J.descrever(meuResultado)}`);
  if (meuResultado === J.VITORIA) return terminar(false);
  suaVezAgora();
}

async function rodadaAudio(partida, alvo) {
  const { venceu, tiroDele } = await meuTurnoAudio(partida, alvo);
  if (venceu) return terminar(true);
  await turnoDeleESeguir(partida, tiroDele);
}

async function turnoDeleESeguir(partida, tiroDele = null) {
  if (await turnoDeleAudio(partida, tiroDele)) return terminar(false);
  suaVezAgora();
}

/**
 * Atira e espera a resposta, repetindo o tiro quando pedido. tiroDele vem
 * preenchido quando, em vez da resposta, chega um tiro: o adversário ouviu o
 * nosso, respondeu, a resposta se perdeu e ele já está na vez dele.
 */
async function meuTurnoAudio(partida, alvo) {
  const coord = J.fmtCoord(alvo);
  let tentativa = 1, resposta;
  status(`Transmitindo ${coord}... silêncio na sala`);
  await enviar(partida, coord);
  for (;;) {
    status(`Aguardando a resposta ao tiro em ${coord}${tentativa > 1 ? ` (tentativa ${tentativa})` : ''}...`);
    resposta = await ouvir(partida, 'a resposta', `Repetir tiro ${coord}`);
    partida.checar();
    if (resposta === REPETIR) {
      tentativa++;
      log(`Repetindo o tiro em ${coord} (tentativa ${tentativa}).`);
      status(`Transmitindo ${coord} de novo... silêncio na sala`);
      await enviar(partida, coord);
      continue;
    }
    if (resposta === null) {
      log(`Você desistiu de esperar a resposta ao tiro em ${coord}. Jogada perdida.`, 'aviso');
      return { venceu: false, tiroDele: null };
    }
    if (J.RESPOSTAS.includes(resposta)) break;
    const tiroDele = J.lerCoord(resposta);
    if (tiroDele !== null) {
      partida.ultimoMira = null;
      desenhar();
      log(`Chegou um tiro em vez da resposta: o adversário ouviu o seu tiro em ${coord}, mas a resposta ` +
          'se perdeu. Você pode atirar lá de novo depois.', 'aviso');
      return { venceu: false, tiroDele };
    }
    log(`Resposta ilegível (chegou '${resposta}'). Continuo ouvindo: repita o tiro ou peça ao adversário ` +
        'para repetir a resposta.', 'aviso');
  }
  partida.mira.set(alvo, resposta);
  partida.ultimoMira = alvo;
  desenhar();
  log(`Seu tiro em ${coord}: ${J.descrever(resposta)}`);
  return { venceu: resposta === J.VITORIA, tiroDele: null };
}

/** Recebe um tiro e responde. Um tiro ilegível não encerra a espera: o adversário pode repeti-lo. */
async function turnoDeleAudio(partida, cel = null) {
  while (cel === null) {
    status('Aguardando o tiro do adversário...', 'espera');
    const texto = await ouvir(partida, 'o tiro do adversário');
    partida.checar();
    if (texto === null) {
      log('Você desistiu de esperar o tiro. A vez passa para você.', 'aviso');
      return false;
    }
    cel = J.lerCoord(texto);
    if (cel !== null) break;
    log(J.RESPOSTAS.includes(texto)
      ? `Chegou uma resposta repetida ('${texto}'), não um tiro. Continuo esperando o tiro.`
      : `Não entendi o tiro (chegou '${texto}'). Continuo ouvindo: o adversário pode repetir o tiro.`, 'aviso');
  }
  partida.iniciou = true;
  const resultado = partida.meu.receberTiro(cel);
  partida.ultimoMeu = cel;
  partida.ultimaResposta = resultado;
  desenhar();
  log(`O adversário atirou em ${J.fmtCoord(cel)}: ${J.descrever(resultado)}`);
  status('Respondendo...');
  await enviar(partida, resultado);
  return resultado === J.VITORIA;
}

async function repetirResposta(partida) {
  const resposta = partida.ultimaResposta;
  log(`Repetindo a resposta '${resposta}' (${J.descrever(resposta)}).`);
  status('Repetindo a resposta... silêncio na sala');
  await enviar(partida, resposta);
  suaVezAgora();
}

// ================================================================== botões

function atirar(id) {
  if (!p || p.fim || !ui.vez || p.mira.has(id)) return;
  p.iniciou = true;
  p.ultimoMira = id;
  p.ultimaResposta = null;
  ui.vez = false;
  modoRepetir(null);
  desenhar();
  rodar(p.modo === 'cpu' ? rodadaCpu : rodadaAudio, id);
}

$('#btn-repetir').onclick = () => {
  if (!p || p.fim) return;
  if (ui.repetirModo === 'tiro' && p.espera) {
    $('#btn-repetir').disabled = true;
    p.espera(REPETIR);
  } else if (ui.repetirModo === 'resposta' && ui.vez && p.ultimaResposta) {
    ui.vez = false;
    modoRepetir(null);
    desenhar();
    rodar(repetirResposta);
  }
};

$('#btn-parar').onclick = () => { if (p && p.espera) p.espera(null); };

$('#btn-sortear').onclick = () => {
  if (!p || p.iniciou || p.fim) return;
  p.meu = new J.Tabuleiro();
  desenhar();
};

$('#btn-nova').onclick = () => {
  if (p && !p.fim && p.iniciou && !confirm('Abandonar a partida em andamento?')) return;
  encerrar();
  manterTelaLigada(false);
  $('#jogo').hidden = true;
  $('#inicio').hidden = false;
  window.scrollTo(0, 0);
};

// ---- tela inicial

const form = $('#form-inicio');
function ajustarOpcoes() {
  const cpu = form.modo.value === 'cpu';
  $('#op-cpu').hidden = !cpu;
  $('#op-audio').hidden = cpu;
}
form.addEventListener('change', ajustarOpcoes);

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const modo = form.modo.value;
  const opcoes = {
    ouvir: modo === 'cpu' && $('#ouvir').checked,
    ruidoDb: modo === 'cpu' && $('#ruido').value.trim() !== '' ? Number($('#ruido').value.replace(',', '.')) : null,
    volMax: $('#volmax').checked,
  };
  const erroInicio = $('#erro-inicio');
  erroInicio.hidden = true;
  if (opcoes.ruidoDb !== null && !Number.isFinite(opcoes.ruidoDb)) {
    erroInicio.textContent = 'O ruído precisa ser um número (em dB).';
    erroInicio.hidden = false;
    return;
  }
  $('#comecar').disabled = true;
  try {
    if (modo !== 'cpu' || opcoes.ouvir) await audio.preparar(modo !== 'cpu');
  } catch (e) {
    erroInicio.textContent = e.message;
    erroInicio.hidden = false;
    return;
  } finally {
    $('#comecar').disabled = false;
  }
  if (modo !== 'cpu') manterTelaLigada(true);
  iniciarPartida(modo, opcoes);
});

function iniciarPartida(modo, opcoes) {
  encerrar();
  p = new Partida(modo, opcoes);
  $('#log').replaceChildren();
  faseParado();
  ui.vez = false;

  if (modo === 'cpu') {
    const extras = [opcoes.ouvir ? 'tocando o áudio' : 'sem áudio'];
    if (opcoes.ruidoDb !== null) extras.push(`ruído a ${opcoes.ruidoDb} dB`);
    $('#chip').textContent = `contra o computador · ${extras.join(', ')}`;
    $('#mic').hidden = true;
    log(`Partida contra o computador. ${J.NAVIOS.length} navios de cada lado: ` +
        J.NAVIOS.map(([n, t]) => `${n} (${t})`).join(', ') + '.');
  } else {
    $('#chip').textContent = `${modo === 'anfitriao' ? 'anfitrião' : 'convidado'} · áudio a ${audio.Fs} Hz`;
    $('#mic').hidden = false;
    log('Áudio real. Aparelhos próximos, volume alto, sala em silêncio.');
    log('Os dois aparelhos precisam começar a partida antes do primeiro tiro.');
  }
  $('#inicio').hidden = true;
  $('#jogo').hidden = false;
  window.scrollTo(0, 0);
  desenhar();
  if (modo === 'convidado') rodar(turnoDeleESeguir);
  else suaVezAgora();
}

document.addEventListener('visibilitychange', () => {
  if (!p || p.modo === 'cpu' || p.fim) return;
  if (document.visibilityState === 'visible') {
    manterTelaLigada(true);
    if (p.espera) log('A página saiu da tela. Se o navegador pausou o microfone, uma mensagem pode ter se perdido.', 'aviso');
  }
});

// ================================================================ desenho

function montarGrade(el, clicavel) {
  el.replaceChildren(document.createElement('span'));
  for (const c of J.COLS) el.append(Object.assign(document.createElement('span'), { className: 'eixo', textContent: c }));
  el.celulas = [];
  for (let l = 0; l < J.N; l++) {
    el.append(Object.assign(document.createElement('span'), { className: 'eixo', textContent: l + 1 }));
    for (let c = 0; c < J.N; c++) {
      const id = J.casa(c, l), b = document.createElement('button');
      b.type = 'button';
      b.className = 'celula';
      if (clicavel) b.onclick = () => atirar(id);
      else b.tabIndex = -1;
      el.append(b);
      el.celulas[id] = b;
    }
  }
}
montarGrade($('#grade-mira'), true);
montarGrade($('#grade-meu'), false);

const NOME_ESTADO = { vazio: 'vazia', navio: 'navio', atingido: 'navio atingido', afundado: 'navio afundado',
                      acerto: 'acerto', agua: 'água', oculto: 'navio inimigo' };

function pintar(el, estados, ultimo, ativa) {
  el.classList.toggle('ativa', ativa);
  for (let id = 0; id < J.N * J.N; id++) {
    const est = estados.get(id) || 'vazio', b = el.celulas[id];
    b.className = `celula ${est}${id === ultimo ? ' ultimo' : ''}`;
    b.setAttribute('aria-label', `${J.fmtCoord(id)}, ${NOME_ESTADO[est]}`);
    if (el.id === 'grade-mira') b.disabled = !(ativa && est === 'vazio');
  }
}

function desenhar() {
  if (!p) return;
  const meu = new Map(), frota = [];
  for (const navio of p.meu.navios) {
    const afund = J.afundado(navio);
    for (const id of navio.celulas) meu.set(id, afund ? 'afundado' : navio.atingidas.has(id) ? 'atingido' : 'navio');
    const n = navio.atingidas.size, t = navio.celulas.size;
    frota.push(`${navio.nome} (${t}): ${afund ? 'afundado' : n ? `atingido ${n}/${t}` : 'intacto'}`);
  }
  for (const id of p.meu.tirosRecebidos) if (!meu.has(id)) meu.set(id, 'agua');

  const simbolo = { [J.AGUA]: 'agua', [J.ACERTO]: 'acerto', [J.AFUNDOU]: 'afundado', [J.VITORIA]: 'afundado' };
  const mira = new Map([...p.mira].map(([id, r]) => [id, simbolo[r]]));
  if (p.fim && p.dele) for (const id of p.dele.ocupadas()) if (!mira.has(id)) mira.set(id, 'oculto');
  const respostas = [...p.mira.values()];
  const afundados = respostas.filter((r) => r === J.AFUNDOU || r === J.VITORIA).length;

  pintar($('#grade-meu'), meu, p.ultimoMeu, false);
  pintar($('#grade-mira'), mira, p.ultimoMira, ui.vez);
  $('#info-meu').textContent = frota.join('\n');
  $('#info-mira').textContent = `Navios inimigos afundados: ${afundados} de ${J.NAVIOS.length}\n` +
    `Tiros: ${respostas.length}   acertos: ${respostas.filter((r) => r !== J.AGUA).length}`;
  $('#btn-sortear').disabled = p.iniciou || !!p.fim;
}

function suaVezAgora() {
  ui.vez = true;
  status('Sua vez: toque numa casa do tabuleiro inimigo', 'vez');
  desenhar();
  if (p.modo !== 'cpu' && p.ultimaResposta) {
    modoRepetir('resposta', `Repetir resposta (${J.descrever(p.ultimaResposta)})`);
  }
}

function terminar(venceu) {
  p.fim = venceu ? 'vitoria' : 'derrota';
  ui.vez = false;
  modoRepetir(null);
  faseParado();
  desenhar();
  manterTelaLigada(false);
  if (venceu) {
    status('VOCÊ VENCEU! A partida inteira passou por som.', 'vitoria');
    log('Você venceu.', 'fim');
  } else {
    const quem = p.modo === 'cpu' ? 'O computador' : 'O adversário';
    status(`${quem.toUpperCase()} VENCEU.`, 'derrota');
    log(`${quem} venceu.`, 'aviso');
  }
}

function erro(e) {
  faseParado();
  ui.vez = false;
  modoRepetir(null);
  desenhar();
  log(`Erro: ${e.message || e}`, 'erro');
  status('Erro no áudio. Veja o registro e comece uma nova partida.', 'derrota');
}

function status(texto, tom = '') {
  const el = $('#status');
  el.textContent = texto;
  el.className = tom;
}

function log(texto, tag = '') {
  const li = document.createElement('li'), hora = document.createElement('span');
  hora.className = 'hora';
  hora.textContent = new Date().toLocaleTimeString('pt-BR');
  const msg = document.createElement('span');
  msg.className = tag;
  msg.textContent = texto;
  li.append(hora, msg);
  const lista = $('#log');
  lista.append(li);
  while (lista.children.length > 300) lista.firstChild.remove();
  lista.scrollTop = lista.scrollHeight;
}

// ---- painel do modem

function modoRepetir(modo, rotulo) {
  ui.repetirModo = modo;
  ui.dicaDada = false;
  if (rotulo) $('#btn-repetir').textContent = rotulo;
  $('#btn-repetir').disabled = !modo;
}

function faseTx(texto, dur) {
  ui.anim = { inicio: performance.now(), dur };
  ui.ouvindoDesde = null;
  ui.textoFase = `TRANSMITINDO '${texto}' · ${dur.toFixed(1)}s`;
  $('#barra').classList.remove('ocupada');
  $('#btn-parar').disabled = true;
  modoRepetir(null);
}

function faseRx(oque, repetirRotulo) {
  ui.anim = null;
  ui.ouvindoDesde = performance.now();
  ui.textoFase = `OUVINDO ${oque}`;
  $('#barra').classList.add('ocupada');
  $('#btn-parar').disabled = false;
  modoRepetir(repetirRotulo ? 'tiro' : null, repetirRotulo);
}

function faseOcupado(texto) {
  ui.anim = null;
  ui.ouvindoDesde = null;
  ui.textoFase = texto;
  $('#barra').classList.add('ocupada');
  $('#btn-parar').disabled = true;
  modoRepetir(null);
}

function faseParado() {
  ui.anim = null;
  ui.ouvindoDesde = null;
  ui.textoFase = 'parado';
  $('#barra').classList.remove('ocupada');
  $('#barra i').style.width = '0';
  $('#btn-parar').disabled = true;
  modoRepetir(null);
  nivel(0, '');
}

function nivel(rms, tom) {
  const db = 20 * Math.log10(Math.max(rms, 1) / 32768);       // dBFS
  $('#nivel').style.width = `${Math.min(100, Math.max(0, (db + 80) / 80 * 100))}%`;
  $('#tom').textContent = tom;
}

setInterval(() => {
  const agora = performance.now();
  let texto = ui.textoFase;
  if (ui.anim) {
    $('#barra i').style.width = `${Math.min(100, (agora - ui.anim.inicio) / 10 / ui.anim.dur)}%`;
  } else if (ui.ouvindoDesde) {
    const ouvindo = (agora - ui.ouvindoDesde) / 1000;
    texto += ` · ${ouvindo.toFixed(0)}s`;
    if (ui.repetirModo === 'tiro') {
      // Repetir por cima de uma resposta que está chegando estragaria as duas.
      const chegando = agora - ui.ultimoTom < TOM_RECENTE_S * 1000;
      if (chegando) texto += ' · recebendo sinal';
      $('#btn-repetir').disabled = chegando;
      if (ouvindo > DICA_SEM_RESPOSTA_S && !chegando && !ui.dicaDada) {
        ui.dicaDada = true;
        status(`Sem resposta há ${DICA_SEM_RESPOSTA_S}s. Se o adversário não ouviu o tiro, toque em Repetir tiro.`, 'vez');
      }
    }
  }
  $('#fase').textContent = texto;
}, 100);

ajustarOpcoes();

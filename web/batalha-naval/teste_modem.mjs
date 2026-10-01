// Testes do modem em JavaScript, sem hardware:  node teste_modem.mjs
//
// Todas as mensagens do jogo (36 coordenadas e 4 respostas) passam pelo
// receptor inteiro em loopback, nas duas taxas de amostragem comuns em PC e
// celular, com canal limpo e com ruído.

import { loopback, textoParaSinal, normalizar, sinalParaTexto, PARAMS } from './modem.js';
import { COLS, N, RESPOSTAS } from './jogo.js';

let falhas = 0;
function checar(ok, descricao, detalhe = '') {
  console.log(`  [${ok ? 'ok' : 'FALHOU'}] ${descricao}${detalhe ? '  ' + detalhe : ''}`);
  if (!ok) falhas++;
}

function semente(s) {   // mulberry32: ruído reproduzível
  return () => {
    s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const mensagens = [...RESPOSTAS];
for (const c of COLS) for (let l = 1; l <= N; l++) mensagens.push(c + l);

for (const Fs of [48000, 44100]) {
  console.log(`\nFs = ${Fs} Hz`);
  const t0 = performance.now();
  const erradas = mensagens.filter((m) => loopback(textoParaSinal(m, Fs), Fs) !== m);
  checar(erradas.length === 0, `${mensagens.length} mensagens do jogo, canal limpo`,
         erradas.length ? `erraram: ${erradas.join(' ')}` : `${((performance.now() - t0) / mensagens.length).toFixed(0)} ms por mensagem`);

  for (const snr of [25, 15]) {
    const rng = semente(snr);
    const erradas = mensagens.filter((m) => loopback(textoParaSinal(m, Fs), Fs, { ruidoDb: snr, rng }) !== m);
    checar(erradas.length <= (snr >= 25 ? 0 : 2), `ruído a ${snr} dB`, `${mensagens.length - erradas.length}/${mensagens.length} certas`);
  }

  // A gravação real começa no meio do silêncio e não no início do arquivo.
  const sinal = normalizar(textoParaSinal('B3', Fs));
  const deslocado = new Float64Array(sinal.length + 12345);
  deslocado.set(sinal, 12345);
  checar(sinalParaTexto(deslocado, Fs) === 'B3', 'decodifica com o início deslocado');
  checar(sinalParaTexto(new Float64Array(Fs * 3), Fs) === '', 'silêncio não vira mensagem');
}

const durB3 = textoParaSinal('B3', 48000).length / 48000;
checar(Math.abs(durB3 - 7.1) < 0.01, 'tiro dura 7,1 s, como no Python', `${durB3.toFixed(2)} s`);
console.log(`\nperfil: sync ${PARAMS.syncDur}s em ${PARAMS.fsync} Hz, dados ${PARAMS.fbase}-${PARAMS.fbase + 15 * PARAMS.step_hz} Hz`);
console.log(falhas ? `\n${falhas} FALHA(S)` : '\nTudo certo.');
process.exit(falhas ? 1 : 0);

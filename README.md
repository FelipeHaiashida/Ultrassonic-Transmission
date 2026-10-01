# Ultrassonic Transmission

Transmissão de dados por **som**, sem rede, cabo ou Bluetooth: um lado
transforma texto (ou um arquivo) em tons de áudio, o outro escuta pelo
microfone e reconstrói a mensagem original.

O projeto evoluiu em versões sucessivas, cada uma numa pasta própria. A mais
completa e mais fácil de testar é a **v4** — comece por ela.

## Comece por aqui: [`v4/`](v4/)

```bash
git clone https://github.com/aomaaj/Ultrassonic-Transmission.git
cd Ultrassonic-Transmission
pip install -r requirements.txt
cd v4
python loopback_test.py      # roda toda a suíte de testes, sem hardware nenhum
python batalha_naval.py --ouvir   # joga contra a CPU e toca o áudio de verdade
```

`sounddevice` (já incluído no `requirements.txt`) só é necessário para ouvir
o áudio de verdade (`--ouvir` ou `--audio`). Os testes e o modo padrão do
jogo rodam só com `numpy` e `scipy`, sem precisar de microfone ou
alto-falante — veja [`v4/README.md`](v4/README.md) para todos os detalhes e
comandos.

## Estrutura do repositório

| Pasta               | O que é                                                                 |
| -------------------- | ------------------------------------------------------------------------ |
| raiz (`emissor.py` etc.) | Primeiro protótipo: transmissão de um arquivo de texto por som quase-ultrassônico (19–21,5 kHz). |
| [`v2/`](v2/README.md)   | Mesmo protocolo, revisado e documentado.                              |
| [`v3/`](v3/README.md)   | Vira jogo: Batalha Naval jogado inteiramente por som, com modo loopback (testa sem hardware) e suíte de testes automatizados. |
| [`v4/`](v4/README.md)   | **Recomendada.** Mesmo protocolo da v3, só que numa faixa de som **audível** (1–3,5 kHz) — pensada para facilitar teste e depuração antes de migrar para a faixa quase-ultrassônica definitiva. |
| [`diagnostico/`](diagnostico/README.md) | Ferramenta para testar a transmissão entre 2 PCs e explicar, etapa por etapa, por que deu certo ou errado. |
| [`docs/`](docs/)        | Documentação do projeto (Termo de Abertura, Planejamento de Entregáveis, Documentação da Fase 1), em português. |

Cada pasta de versão tem seu próprio `README.md` com os detalhes de como o
protocolo funciona e como rodar aquela versão especificamente.

## Como testar comigo

A forma mais rápida de validar que está tudo funcionando, sem precisar de
microfone nem de outra pessoa:

```bash
cd v4
python loopback_test.py
```

Para testar a transmissão de verdade entre dois computadores (ou duas
pessoas), use o modo `--audio`:

```bash
python batalha_naval.py --audio anfitriao   # numa máquina
python batalha_naval.py --audio convidado   # na outra
```

Se não funcionar entre as duas máquinas, rode o
[diagnóstico](diagnostico/README.md) (`receber` num PC, `emitir` no outro) e
anexe o `.wav` e o relatório gerados.

Encontrou um problema? Abra uma [issue](../../issues) descrevendo o que
rodou e o que aconteceu — inclui, se possível, a saída do terminal.

## Licença

Este projeto está sob a licença [MIT](LICENSE).

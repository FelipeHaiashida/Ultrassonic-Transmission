import transfer_lib as tl
import os

print("--- RECEPTOR ---")
print("Aguardando sinal de áudio...")

# 1. Grava o áudio
audio_gravado = tl.record()

print("Sinal recebido! Salvando o arquivo...")

# 2. Decodifica e grava o resultado no disco
tl.writeSignalToFile(audio_gravado, outputFile='mensagem_recebida.txt')

# Verificação final
if os.path.exists('mensagem_recebida.txt'):
    print("\nSUCESSO TOTAL! 🏆")
    print("O arquivo 'mensagem_recebida.txt' foi criado na pasta.")
    print("Pode abrir para conferir.")
else:
    print("Erro: Não consegui criar o arquivo de texto.")

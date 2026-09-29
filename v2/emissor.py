import transfer_lib as tl
import os

# 1. Cria o arquivo de texto
print("--- EMISSOR ---")
texto = "Vamos para a praia"
with open('mensagem.txt', 'w') as f:
    f.write(texto)
print("1. Arquivo mensagem.txt criado.")

# 2. Gera o WAV
print("2. Gerando áudio... (Isso pode levar alguns segundos)")
try:
    tl.writeFileToWav('mensagem.txt', outputFile='transmissao.wav')

    # Verifica se criou mesmo
    if os.path.exists('transmissao.wav'):
        tamanho = os.path.getsize('transmissao.wav')
        print(f"3. SUCESSO! Arquivo 'transmissao.wav' gerado ({tamanho/1024:.1f} KB).")
        print("   -> Abra este arquivo no Media Player e prepare o Receptor.")
    else:
        print("ERRO: O arquivo não apareceu na pasta.")
except Exception as e:
    print(f"ERRO FATAL: {e}")

# Baixa o modelo Kokoro e a voz francesa durante o build,
# para o primeiro áudio não demorar nem depender de internet.
from kokoro import KPipeline

pipeline = KPipeline(lang_code="f", repo_id="hexgrad/Kokoro-82M")
for _ in pipeline("Bonjour.", voice="ff_siwis"):
    pass

print("Modelo Kokoro (ff_siwis) pronto.")

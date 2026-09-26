import argostranslate.package as pkg

pkg.update_package_index()
available = pkg.get_available_packages()

def install(f, t):
    p = next((x for x in available if x.from_code == f and x.to_code == t), None)
    if p:
        pkg.install_from_path(p.download())
    return p is not None

if not install("fr", "pt"):
    if not (install("fr", "en") and install("en", "pt")):
        raise RuntimeError("Modelos fr→pt (ou fr→en→pt) não encontrados.")
print("Modelos instalados.")

# Baixa o modelo e a voz francesa na hora do build, para funcionar offline
from kokoro import KPipeline
pipe = KPipeline(lang_code="f", repo_id="hexgrad/Kokoro-82M")
for _ in pipe("Bonjour.", voice="ff_siwis"):
    pass
print("Kokoro pronto.")

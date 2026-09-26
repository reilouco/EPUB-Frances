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

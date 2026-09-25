import argostranslate.package

argostranslate.package.update_package_index()
packages = argostranslate.package.get_available_packages()

model = next(
    (
        package
        for package in packages
        if package.from_code == "fr" and package.to_code == "pt"
    ),
    None,
)

if model is None:
    raise RuntimeError("Modelo Argos Translate francês -> português não encontrado.")

download_path = model.download()
argostranslate.package.install_from_path(download_path)

print("Modelo francês -> português instalado com sucesso.")
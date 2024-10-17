from setuptools import setup, find_packages

setup(
    name="ds_download",                         # Nombre del paquete
    version="0.1.0",                           # Versión del paquete
    description="Descripción de tu paquete",   # Breve descripción
    long_description=open('README.md').read(), # Descripción larga (opcional)
    long_description_content_type='text/markdown',  # Tipo de contenido de README
    author="Tu Nombre",                        # Tu nombre o nombre del autor
    author_email="tuemail@example.com",        # Tu email de contacto
    packages=find_packages(),                  # Encuentra automáticamente los paquetes
    classifiers=[                              # Clasificadores opcionales
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.10',                   # Requiere Python 3.6 o superior
    install_requires=[                         # Dependencias (si las hay)
        "geojson==2.5.0",
        "google-api-core==2.10.1",
        "google-auth==2.11.1",
        "google-cloud-core==2.3.2",
        "google-cloud-storage==2.5.0",
        "google-crc32c==1.5.0",
        "google-resumable-media==2.3.3",
        "googleapis-common-protos==1.56.4",
        "minio==7.2.8",
        "pymongo==4.8.0",
        "requests==2.32.3",
        "rasterio==1.3.11",
        "python-dotenv==1.0.1",
        "geomet==1.1.0",
        "python-dateutil==2.9.0",
    ],
)

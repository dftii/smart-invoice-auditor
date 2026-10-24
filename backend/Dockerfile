# Imagen del backend de SmartInvoice-FACe Auditor.
# Construir y ejecutar desde la raíz del repositorio con: docker compose up --build
#
# Build en dos fases: el builder genera las ruedas (wheels) y las instala en /opt/venv;
# la imagen final solo copia ese entorno virtual, sin herramientas de compilación.

# --- Fase 1: dependencias ---
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /build

COPY requirements.txt .
RUN python -m pip wheel --wheel-dir /wheels -r requirements.txt \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-index --find-links=/wheels -r requirements.txt

# --- Fase 2: imagen de ejecución ---
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Usuario sin privilegios para ejecutar la aplicación.
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

COPY app ./app
COPY scripts ./scripts
COPY samples ./samples

# Directorio de datos (SQLite y facturas subidas), escribible solo por el usuario de la aplicación.
# Un volumen con nombre copia en su primera creación el propietario de esta carpeta.
RUN mkdir -p /app/data/uploads \
    && chown -R app:app /app/data

VOLUME ["/app/data"]

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

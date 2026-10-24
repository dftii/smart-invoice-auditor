# SmartInvoice-FACe Auditor

Sistema de extracción y auditoría fiscal de facturas españolas. Recibe facturas en PDF o imagen, extrae sus datos con IA (Claude) o con un parser demo sin dependencias externas, comprueba la coherencia fiscal (NIF/CIF, IVA, IRPF, fechas, importes y duplicados) y genera el XML Facturae 3.2.2 listo para enviar a FACe.

## Características

- **Extracción de datos** de facturas en PDF, PNG, JPG y WEBP mediante un patrón *strategy*: Claude Vision o un parser demo local.
- **Auditoría fiscal** con reglas específicas del marco español: validación de NIF, NIE y CIF con su algoritmo de control, tipos de IVA (21, 10 y 4 %) y de retención IRPF, cuadre de bases, cuotas y totales al céntimo.
- **Detección de duplicados** por emisor y número de factura, y por hash del fichero original.
- **Trazabilidad completa**: cada ejecución de la auditoría guarda un registro por regla, incluidas las superadas, con los importes esperados y encontrados.
- **Exportación Facturae 3.2.2** en XML, con firma XAdES opcional mediante certificado PKCS#12.
- **Exportación CSV** con separador `;` y BOM UTF-8, pensada para abrirse en Excel con configuración española.
- **API REST** documentada con OpenAPI, con estadísticas agregadas para un dashboard.
- **Base de datos** SQLite por defecto y MySQL 8.4 mediante una simple variable de entorno.
- **Contenedores** con Docker y Docker Compose, con usuario no privilegiado y healthcheck.

## Arquitectura

```mermaid
flowchart LR
    U["Subida de factura<br/>PDF / PNG / JPG / WEBP"] --> E{"Extractor<br/>(strategy)"}
    E -->|"ANTHROPIC_API_KEY definida"| AI["Claude Vision<br/>extracción con IA"]
    E -->|"sin clave de API"| DEMO["Parser demo<br/>pypdf + regex"]
    AI -.->|"fallback si falla"| DEMO
    AI --> DB[("Base de datos<br/>SQLite / MySQL")]
    DEMO --> DB
    DB --> AUD["Auditor<br/>reglas fiscales"]
    AUD --> FAC["Exportador Facturae 3.2.2<br/>XML para FACe"]
    AUD --> CSV["Exportador CSV"]
    AUD --> DASH["Dashboard<br/>estadísticas"]
```

El flujo de una factura es lineal: el extractor produce un resultado normalizado, el servicio de facturas lo persiste junto con el proveedor, el auditor ejecuta todas las reglas y fija el estado de la factura, y los exportadores solo trabajan sobre facturas validadas o sobre el listado completo.

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.11+ (imagen Docker con Python 3.12) |
| API | FastAPI, Uvicorn, Pydantic 2 y pydantic-settings |
| Persistencia | SQLAlchemy 2.0, SQLite (por defecto) o MySQL 8.4 con PyMySQL |
| Extracción | Anthropic SDK (Claude Vision), pypdf, Pillow |
| Facturae | lxml, signxml (firma XAdES), cryptography |
| Exportación CSV | Biblioteca estándar de Python |
| Tests | pytest, pytest-cov, httpx, Ruff |
| Contenedores | Docker, Docker Compose |

## Estructura del proyecto

```
smart-invoice-auditor/
├── backend/
│   ├── app/
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── logging.py
│   │   │   └── errors.py
│   │   ├── db/
│   │   │   └── database.py
│   │   ├── routers/
│   │   │   ├── invoices.py
│   │   │   ├── suppliers.py
│   │   │   └── stats.py
│   │   ├── services/
│   │   │   ├── extractor.py
│   │   │   ├── auditor.py
│   │   │   ├── tax_id.py
│   │   │   ├── face_exporter.py
│   │   │   ├── invoice_service.py
│   │   │   ├── csv_exporter.py
│   │   │   └── stats_service.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   └── main.py
│   ├── scripts/
│   │   ├── generate_sample_invoices.py
│   │   └── seed.py
│   ├── samples/              # facturas PDF/PNG de ejemplo
│   ├── tests/
│   ├── requirements.in
│   ├── requirements-dev.in
│   ├── requirements.txt      # generado con uv, versiones fijadas
│   ├── requirements-dev.txt  # generado con uv, versiones fijadas
│   ├── pytest.ini
│   ├── Dockerfile
│   ├── .dockerignore
│   └── .env.example
├── frontend/                 # Fase 2
├── docker-compose.yml
├── .env.example
└── README.md
```

## Puesta en marcha

### Requisitos

- Python 3.11 o superior.
- Opcional: una clave de API de Anthropic para el Modo IA.
- Opcional: Docker Desktop o Docker Engine con Docker Compose v2.24 o superior (ver [Docker](#docker)).

### Inicio rápido

Todos los comandos se ejecutan desde el directorio `backend/`.

**Windows (PowerShell 5.1 o superior)**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe scripts/generate_sample_invoices.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Estos comandos usan el Python del entorno virtual directamente, sin activarlo. PowerShell 5.1 no admite `&&`, así que cada comando va en su propia línea. Si prefieres activar el entorno con `.\.venv\Scripts\Activate.ps1`, es posible que antes tengas que permitir la ejecución de scripts locales con `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**Linux / macOS**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python scripts/generate_sample_invoices.py
uvicorn app.main:app --reload
```

Abre **http://localhost:8000/docs** para ver y probar la API. El script `generate_sample_invoices.py` crea en `backend/samples/` facturas de prueba: una correcta con IVA del 21 %, una con tipos mixtos 21/10/4, una profesional con retención IRPF del 15 %, una con error aritmético y una con un CIF no válido. Son los ficheros que puedes subir para ver la auditoría en acción.

### Ejecución con Docker

Desde la raíz del repositorio, `docker compose up --build` construye la imagen y arranca la API en http://localhost:8000/docs. Los detalles (datos de demostración, MySQL, volúmenes) están en la sección [Docker](#docker).

### Configuración

Toda la configuración se realiza con variables de entorno o con el fichero `backend/.env`. La lista completa, con sus valores por defecto y comentarios, está en `backend/.env.example`.

## Modo Demo y Modo IA

El extractor elige su estrategia según la variable `ANTHROPIC_API_KEY`.

| Modo | Condición | Cómo extrae los datos |
|---|---|---|
| **Modo Demo** | `ANTHROPIC_API_KEY` vacía (valor por defecto) | Lee el texto del PDF con pypdf y aplica un parser basado en expresiones regulares. No realiza llamadas externas. |
| **Modo IA** | `ANTHROPIC_API_KEY` definida | Envía el documento a Claude Vision (modelo `ANTHROPIC_MODEL`) y obtiene los datos en JSON mediante uso de herramientas. |

Ambos modos devuelven el mismo formato de resultado, así que las reglas de auditoría se aplican igual sobre los dos.

Notas sobre el Modo Demo:

- Funciona con los PDF generados por `generate_sample_invoices.py` y con PDF de texto que sigan un diseño parecido.
- Si el fichero no contiene texto utilizable (imágenes o escaneos), genera una factura sintética determinista a partir del hash del fichero. Sirve para probar el flujo completo, pero **sus datos no proceden del documento**.

En Modo IA, si la llamada a la API falla o supera `AI_TIMEOUT_SECONDS`, la extracción pasa al Modo Demo cuando `AI_FALLBACK_TO_DEMO=true` (valor por defecto) y se añade un aviso al resultado.

## Migración a MySQL

SQLAlchemy abstrae el motor, por lo que el cambio consiste en configurar la URL de conexión. PyMySQL ya está incluido en `requirements.txt`.

1. Crea la base de datos y el usuario en el servidor MySQL 8.4:

   ```sql
   CREATE DATABASE smartinvoice CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'smartinvoice'@'%' IDENTIFIED BY 'elige-una-contraseña-segura';
   GRANT ALL PRIVILEGES ON smartinvoice.* TO 'smartinvoice'@'%';
   FLUSH PRIVILEGES;
   ```

2. Define la URL en `backend/.env`, con el charset `utf8mb4`:

   ```env
   DATABASE_URL=mysql+pymysql://smartinvoice:elige-una-contraseña-segura@localhost:3306/smartinvoice?charset=utf8mb4
   ```

   Si la contraseña contiene caracteres especiales, codifícalos en formato URL.

3. Arranca la aplicación. Las tablas se crean automáticamente al iniciar el servicio.

   ```bash
   uvicorn app.main:app --reload
   ```

Para usar MySQL con Docker Compose, define la URL con el host `mysql` en el `.env` de la raíz y levanta el perfil `mysql`. Los pasos completos están en la sección [Docker](#docker).

**Importante:** no se incluye una herramienta de migración de datos. Una base de datos MySQL nueva empieza vacía; los datos existentes en SQLite no se copian automáticamente.

## Auditoría fiscal

### Reglas

Cada factura se evalúa con las siguientes reglas. Cada una genera un registro en el log de validaciones: los fallos con su severidad y los aciertos como `INFO`.

| Código de regla | Qué comprueba | Severidad |
|---|---|---|
| `SUPPLIER_TAX_ID` | El NIF, NIE o CIF del emisor es válido, incluido su dígito o letra de control. | ERROR |
| `BUYER_TAX_ID` | El NIF, NIE o CIF del receptor es válido. Si falta, se avisa. | ERROR (AVISO si falta) |
| `DATES` | La fecha de vencimiento no es anterior a la de emisión, la de emisión no es futura y la de operación no es posterior a la de emisión. | ERROR (la de operación, AVISO) |
| `HAS_LINES` | La factura contiene al menos una línea. | ERROR |
| `LINE_BASE` | Base de cada línea = cantidad × precio unitario × (1 − descuento), redondeada al céntimo. | ERROR |
| `VAT_RATE` | El tipo de IVA de cada línea está permitido (21, 10 o 4 %). El 0 % se acepta con aviso (exenta o no sujeta). | ERROR (AVISO en 0 %) |
| `LINE_VAT` | Cuota de IVA de cada línea = base × tipo de IVA, al céntimo. | ERROR |
| `IRPF_RATE` | El tipo de retención IRPF de cada línea está permitido. | ERROR |
| `LINE_IRPF` | Retención de cada línea = base × tipo de IRPF, al céntimo. | ERROR |
| `TAX_BASE_SUM` | La suma de las bases de línea coincide con la base imponible total. | ERROR |
| `VAT_TOTAL` | La cuota total de IVA coincide con el cálculo por tipo o con la suma de las cuotas de línea. | ERROR |
| `IRPF_TOTAL` | La retención total de IRPF coincide con el cálculo por tipo o con la suma de las retenciones de línea. | ERROR |
| `GRAND_TOTAL` | Total = base imponible + IVA − IRPF. | ERROR |
| `DUPLICATE` | Ya existe otra factura con el mismo emisor y número, o con el mismo fichero original. | ERROR |
| `CURRENCY` | La moneda es EUR, requisito para FACe. | AVISO |
| `FACE_DIR3` | Están informados los tres códigos DIR3 necesarios para enviar a FACe. | AVISO |
| `FACE_PARTY_DATA` | Emisor y receptor tienen nombre, dirección, código postal de 5 dígitos, población y provincia (exigidos por Facturae). | AVISO |

Las comparaciones de importes son exactas al céntimo. Los mensajes de error indican el valor esperado y el encontrado, por ejemplo: *«Línea 2: la base imponible debería ser 150.00 € (3 × 50.00 €) y figura 155.00 € (diferencia 5.00 €)»*.

### Estados de la factura

| Estado | Significado |
|---|---|
| `VALIDATED` | Ninguna regla con severidad ERROR ha fallado. Puede tener avisos. |
| `DISCREPANCY` | Al menos una regla con severidad ERROR ha fallado. |
| `DUPLICATE` | La factura duplica otra ya registrada. Tiene prioridad sobre `DISCREPANCY`. |

Solo las facturas en estado `VALIDATED` pueden exportarse a Facturae.

## API

Todos los endpoints de negocio cuelgan de `/api/v1`. La documentación interactiva está en `/docs` y el esquema OpenAPI en `/openapi.json`.

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/api/v1/invoices/upload` | Sube una factura (PDF, PNG, JPG o WEBP), extrae sus datos y ejecuta la auditoría. |
| POST | `/api/v1/invoices` | Crea una factura manualmente y la audita. |
| GET | `/api/v1/invoices` | Lista paginada de facturas con filtros. |
| GET | `/api/v1/invoices/{id}` | Detalle de una factura, con sus líneas y el resultado de la auditoría. |
| GET | `/api/v1/invoices/{id}/logs` | Registro completo de validaciones de la factura. |
| PATCH | `/api/v1/invoices/{id}` | Modifica los datos de una factura. |
| DELETE | `/api/v1/invoices/{id}` | Elimina una factura. |
| POST | `/api/v1/invoices/{id}/revalidate` | Vuelve a ejecutar la auditoría de una factura. |
| POST | `/api/v1/invoices/revalidate` | Vuelve a ejecutar la auditoría de varias facturas en lote. |
| GET | `/api/v1/invoices/{id}/facturae` | Descarga el XML Facturae 3.2.2 de una factura validada. |
| GET | `/api/v1/invoices/export/csv` | Exporta el listado de facturas a CSV. |
| GET | `/api/v1/invoices/{id}/file` | Descarga o visualiza el fichero original. |
| GET | `/api/v1/suppliers` | Lista de proveedores. |
| POST | `/api/v1/suppliers` | Crea un proveedor. |
| GET | `/api/v1/suppliers/{id}` | Detalle de un proveedor. |
| PATCH | `/api/v1/suppliers/{id}` | Modifica los datos de un proveedor. |
| GET | `/api/v1/suppliers/{id}/invoices` | Facturas de un proveedor. |
| GET | `/api/v1/stats/dashboard` | Estadísticas agregadas para el dashboard. |
| GET | `/health` | Comprobación de disponibilidad del servicio. |

### Ejemplos con curl

Subir una factura para extraer y auditar sus datos (sustituye la ruta por la de tu fichero):

```bash
curl -X POST http://localhost:8000/api/v1/invoices/upload \
  -F "file=@ruta/a/factura.pdf;type=application/pdf"
```

Descargar el XML Facturae de una factura validada (el parámetro `-OJ` usa el nombre de fichero que devuelve el servidor):

```bash
curl -OJ http://localhost:8000/api/v1/invoices/1/facturae
```

En Windows PowerShell, usa `curl.exe` para evitar el alias de `Invoke-WebRequest`.

## Exportación Facturae y firma XAdES

El exportador genera el XML según el esquema Facturae 3.2.2 a partir de los datos de la factura. Solo acepta facturas en estado `VALIDATED`.

La firma XAdES es opcional y se activa definiendo en `backend/.env`:

```env
FACTURAE_CERT_PATH=/ruta/al/certificado.p12
FACTURAE_CERT_PASSWORD=contraseña-del-certificado
```

El certificado debe estar en formato PKCS#12 (`.p12` o `.pfx`). Si `FACTURAE_CERT_PATH` está vacía, el XML se genera sin firma. **FACe exige facturas firmadas**, por lo que para enviar facturas a FACe es necesario configurar un certificado válido.

## Datos de demostración (seed)

El script `backend/scripts/seed.py` carga datos de demostración en la base de datos configurada en `DATABASE_URL` (SQLite por defecto). Se ejecuta desde `backend/`:

```powershell
.\.venv\Scripts\python.exe scripts/seed.py
.\.venv\Scripts\python.exe scripts/seed.py --reset
```

Opciones:

- `--reset`: elimina los datos existentes antes de cargar los de demostración.
- `--database-url URL`: usa esa base de datos en lugar de la definida en `DATABASE_URL`.

Con Docker, el servicio `seed` ejecuta el mismo script sobre el volumen de datos del backend (ver [Docker](#docker)).

## Docker

Los ficheros de contenedor son `backend/Dockerfile`, `backend/.dockerignore` y `docker-compose.yml`. La imagen se construye en dos fases sobre `python:3.12-slim`, ejecuta la aplicación con un usuario no privilegiado y expone un healthcheck sobre `/health`. Los datos de SQLite y las facturas subidas se guardan en el volumen con nombre `backend-data`, montado en `/app/data`.

### Construir y arrancar

Desde la raíz del repositorio:

```powershell
docker compose up --build
```

Para ejecutarlo en segundo plano, añade `-d`. La API queda disponible en http://localhost:8000/docs.

### Datos de demostración

El servicio `seed` está en el perfil `seed`, así que solo se ejecuta si lo pides. Termina al acabar la carga:

```powershell
docker compose --profile seed run --rm seed
docker compose --profile seed run --rm seed --reset
```

Los argumentos de `run` (como `--reset`) se añaden a `python scripts/seed.py`.

### MySQL

El servicio `mysql` (MySQL 8.4, utf8mb4) está en el perfil `mysql`. Para usarlo, define la URL en el `.env` de la raíz (copia `.env.example`). Dentro de Compose el host es `mysql`, no `localhost`:

```env
DATABASE_URL=mysql+pymysql://smartinvoice:smartinvoice@mysql:3306/smartinvoice?charset=utf8mb4
```

Después, levanta los servicios con el perfil:

```powershell
docker compose --profile mysql up --build -d
```

En Bash también puedes indicar la variable en la propia línea: `DATABASE_URL=mysql+pymysql://smartinvoice:smartinvoice@mysql:3306/smartinvoice?charset=utf8mb4 docker compose --profile mysql up --build`. En PowerShell usa `$env:DATABASE_URL = "..."` antes del comando.

Si usas esa URL, activa siempre el perfil `mysql`, también para el seed: `docker compose --profile mysql --profile seed run --rm seed`. El backend espera a que MySQL supere su healthcheck antes de arrancar.

Para parar los servicios, usa `docker compose --profile mysql down`. Añade `-v` solo si quieres borrar también los volúmenes `backend-data` y `mysql-data`; es irreversible.

### Variables de entorno

- `backend/.env` (opcional) se pasa al contenedor como variables de entorno. Sus rutas deben ser las del contenedor, no rutas de Windows.
- `.env` de la raíz (copia de `.env.example`) solo sustituye variables de Compose: `DATABASE_URL` y las credenciales de `mysql`. Como Compose aplica estas variables después de `backend/.env`, `DATABASE_URL` de la raíz o del shell prevalece sobre la de `backend/.env` dentro de Docker.

## Tests y cobertura

Desde el directorio `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing
```

Los tests usan una base de datos SQLite en memoria y no requieren clave de API de Anthropic. El informe HTML de cobertura, si lo generas con `--cov-report=html`, se escribe en `htmlcov/`, que está fuera de Git.

## Dependencias

Las dependencias se declaran en `backend/requirements.in` (ejecución) y `backend/requirements-dev.in` (desarrollo, que incluye `-r requirements.in`). Los ficheros `requirements.txt` y `requirements-dev.txt` contienen versiones exactas generadas con [uv](https://docs.astral.sh/uv/). No los edites a mano.

Para cambiar una dependencia, edita el `.in` correspondiente y regenera desde `backend/`:

```powershell
uv pip compile --universal --python-version 3.11 requirements.in -o requirements.txt
uv pip compile --universal --python-version 3.11 requirements-dev.in -c requirements.txt -o requirements-dev.txt
uv pip sync requirements-dev.txt
```

La compilación de `requirements-dev.txt` usa `-c requirements.txt`, así que las versiones de ejecución y de desarrollo coinciden. `uv pip sync` deja el entorno exactamente como indica el fichero, por lo que elimina los paquetes que no estén listados. La imagen Docker instala `requirements.txt`, así que tras cambiarlo vuelve a construirla con `docker compose build`.

## Roadmap

- **Fase 1:** backend con extracción, auditoría, exportación Facturae y CSV, API REST y contenedores.
- **Fase 2:** frontend con dashboard en React, Vite y TypeScript para subir facturas, revisar auditorías y descargar exportables.
- **Fase 3:** despliegue y CI, con pipeline de integración continua y despliegue automatizado del contenedor.

## Licencia

Distribuido bajo la licencia MIT. Consulta el fichero [LICENSE](LICENSE) para más detalles.

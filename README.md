<h1 align="center">SmartInvoice-FACe Auditor</h1>

<p align="center">
  <strong>Sube una factura, audítala al céntimo y genera el XML Facturae 3.2.2 para FACe.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/SQLAlchemy-2.0-D71F00" alt="SQLAlchemy 2.0" />
  <img src="https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white" alt="Pydantic v2" />
  <img src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black" alt="React 19" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/Vite-8-646CFF?logo=vite&logoColor=white" alt="Vite" />
  <img src="https://img.shields.io/badge/Tailwind%20CSS-v4-06B6D4?logo=tailwindcss&logoColor=white" alt="Tailwind CSS v4" />
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker" />
  <img src="https://img.shields.io/badge/Facturae-3.2.2-1E3A8A" alt="Facturae 3.2.2" />
  <img src="https://img.shields.io/badge/tests-1264%20backend%20%C2%B7%2091%20frontend-brightgreen" alt="tests: 1264 backend, 91 frontend" />
  <a href="https://github.com/dftii/smart-invoice-auditor/actions/workflows/ci.yml"><img src="https://github.com/dftii/smart-invoice-auditor/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License MIT" />
</p>

<p align="center">
  <a href="https://dftii.github.io/smart-invoice-auditor/"><strong>▶ Probar la demo en vivo</strong></a> — el backend Python real se ejecuta en tu navegador con WebAssembly, sin instalar nada.
</p>

<p align="center">
  <a href="#demo-en-vivo">Demo en vivo</a> ·
  <a href="#el-problema">El problema</a> ·
  <a href="#la-solución">La solución</a> ·
  <a href="#arquitectura">Arquitectura</a> ·
  <a href="#puesta-en-marcha-en-2-comandos">Puesta en marcha</a> ·
  <a href="#interfaz-web">Interfaz web</a> ·
  <a href="#reglas-de-auditoría">Reglas de auditoría</a> ·
  <a href="#api">API</a> ·
  <a href="#docker">Docker</a> ·
  <a href="#english-summary">English summary</a>
</p>

---

## Demo en vivo

**https://dftii.github.io/smart-invoice-auditor/**

GitHub Pages solo sirve archivos estáticos, así que la demo no llama a ningún servidor: descarga [Pyodide](https://pyodide.org) (CPython compilado a WebAssembly) y ejecuta **el mismo backend FastAPI de este repositorio** dentro de un Web Worker, con SQLite en memoria. Arranca vacía, como un primer uso: solo verás las facturas que subas. No es una API simulada: el auditor, el exportador Facturae y la base de datos son el código Python real.

- La primera carga descarga unos 14 MB y tarda unos 10 s; las siguientes, unos 5 s gracias a la caché.
- Los datos viven en memoria y se reinician al recargar la página.
- Por defecto la demo extrae los datos con el parser de texto local (Modo Demo), sin IA.
- **Modo IA con tu propia clave.** Con el botón «Usar mi API key» puedes pegar una API key de Anthropic y Claude leerá las facturas, también imágenes y escaneos. Se usa la misma `ClaudeVisionStrategy` del backend (prompt, herramienta `record_invoice` y normalización), con un cliente que llama a `api.anthropic.com` desde el Web Worker. La clave se guarda solo en tu navegador (en `sessionStorage`, o en `localStorage` si marcas «Recordar»), se envía únicamente a Anthropic y se comprueba con una petición vacía que no consume tokens. Cada factura sí consume tokens de tu cuenta.
- No hay firma XAdES (no hay certificados en el navegador).

El build de la demo (`npm run build:demo` en `frontend/`) empaqueta `backend/app` y las ruedas Python puras en `public/demo/`, y el workflow `.github/workflows/pages.yml` lo publica en cada push a `main`.

## El problema

En muchas pymes y departamentos de contabilidad, cada factura recibida se teclea a mano en el programa de gestión. Ese trabajo arrastra tres problemas:

- **Errores de cálculo.** Bases, cuotas de IVA o retenciones de IRPF redondeadas mal, descuentos aplicados en el sitio equivocado o totales que no cuadran con las líneas.
- **Pagos duplicados.** La misma factura llega por correo dos veces, o con otro nombre de fichero, y acaba contabilizada y pagada dos veces.
- **Obligaciones de envío electrónico.** Las facturas dirigidas a la Administración Pública deben enviarse electrónicamente a través de FACe (Ley 25/2013), en formato Facturae y firmadas. La Ley 18/2022 («Crea y Crece») prevé además la facturación electrónica entre empresas (B2B), que se aplicará cuando entre en vigor, según su desarrollo reglamentario.

## La solución

SmartInvoice-FACe Auditor convierte una factura en PDF o imagen en datos auditados en segundos:

- **Extracción.** Con Claude Vision (Modo IA) o con un parser local que no hace llamadas externas (Modo Demo).
- **Auditoría.** 18 reglas fiscales que comparan cada importe al céntimo, con un registro de cada comprobación, también de las superadas.
- **Exportación.** XML Facturae 3.2.2 con firma XAdES opcional, y CSV preparado para abrirse en Excel.
- **Panel.** Indicadores, tabla filtrable e inspector para revisar, corregir, volver a auditar y exportar cada factura.

## Arquitectura

```mermaid
flowchart TB
    U["Usuario<br/>navegador"] --> UI

    subgraph FE ["Frontend React 19"]
        UI["Interfaz React<br/>Dropzone, Dashboard, Inspector"]
        PROXY["Proxy<br/>nginx en Docker o Vite en desarrollo"]
        UI -->|"/api y /health"| PROXY
    end

    subgraph BE ["Backend FastAPI"]
        R["Routers<br/>invoices, suppliers, stats"]
        EX{"InvoiceExtractor<br/>strategy"}
        CL["ClaudeVisionStrategy<br/>si hay ANTHROPIC_API_KEY"]
        DM["DemoParserStrategy<br/>pypdf y regex, o factura sintética"]
        SVC["invoice_service<br/>upsert de proveedor y persistencia"]
        AUD["InvoiceAuditor<br/>NIF/NIE/CIF, líneas, IVA 21/10/4,<br/>IRPF, totales, duplicados, DIR3, datos de partes"]
        LOG["ValidationLog<br/>traza por regla y por ejecución"]
        ST{"Estado de la factura"}
        FX["FacturaeExporter"]
        XAD["Firma XAdES<br/>opcional, certificado PKCS#12"]
        CSV["Exportador CSV"]
        STATS["stats_service"]

        R --> EX
        EX -->|"Modo IA"| CL
        EX -->|"Modo Demo"| DM
        CL -.->|"fallback si falla"| DM
        CL --> SVC
        DM --> SVC
        SVC --> AUD
        AUD --> LOG
        AUD --> ST
        ST -->|"VALIDATED"| FX
        FX --> XAD
        R --> CSV
        R --> STATS
    end

    PROXY --> R
    SVC --> ORM["SQLAlchemy 2.0"]
    ORM --> DB[("SQLite o MySQL 8.4")]
    FX -->|"XML Facturae 3.2.2"| FACE["FACe"]
    ST -.->|"DISCREPANCY o DUPLICATE: revisión en la interfaz"| UI
    CSV --> UI
    STATS -->|"indicadores del dashboard"| UI
```

El flujo de una factura es lineal. El extractor produce un resultado normalizado, el servicio de facturas lo persiste junto con el proveedor, el auditor ejecuta todas las reglas y fija el estado, y los exportadores solo trabajan sobre facturas validadas o sobre el listado completo. Las dos estrategias de extracción devuelven el mismo formato, así que la auditoría es idéntica en ambos modos.

## Flujo de subida

```mermaid
sequenceDiagram
    actor U as Usuario
    participant B as Navegador React
    participant P as Proxy nginx
    participant A as API FastAPI
    participant X as InvoiceExtractor
    participant D as Base de datos
    participant V as InvoiceAuditor

    U->>B: Arrastra una factura PDF o imagen
    B->>B: Muestra la animación de escaneo
    B->>P: POST /api/v1/invoices/upload (multipart con progreso)
    P->>A: Reenvía la petición (límite 16 MB)
    A->>A: Valida tamaño y tipo MIME
    A->>X: extract(contenido, nombre, tipo)
    alt Modo IA disponible
        X->>X: Claude Vision devuelve JSON mediante herramientas
    else Modo Demo o fallback
        X->>X: pypdf y expresiones regulares
    end
    X-->>A: Resultado normalizado y avisos
    A->>D: Guarda proveedor, factura y líneas
    A->>V: audit(factura)
    V->>D: Registra un ValidationLog por regla
    V-->>A: Estado y reglas fallidas
    A->>D: Commit de la transacción
    A-->>P: 201 con factura, auditoría y método de extracción
    P-->>B: Respuesta JSON
    B->>U: Revela la comprobación en tiempo real y el estado final
```

## Puesta en marcha en 2 comandos

Necesitas Git y Docker con Docker Compose v2.24 o superior. Con estos dos comandos tienes la aplicación completa, con la base de datos vacía:

```bash
git clone https://github.com/dftii/smart-invoice-auditor.git && cd smart-invoice-auditor
docker compose up -d --build
```

En Windows PowerShell 5.1, que no admite `&&`, usa punto y coma en la primera línea:

```powershell
git clone https://github.com/dftii/smart-invoice-auditor.git; cd smart-invoice-auditor
docker compose up -d --build
```

Después abre:

- **Interfaz web:** http://localhost:8080
- **Documentación interactiva de la API:** http://localhost:8000/docs

Opcional, solo para desarrollo: `docker compose --profile seed run --rm seed` carga 25 facturas de ejemplo (ver [Datos de ejemplo](#datos-de-ejemplo-seed-opcional)).

## Sin Docker

### Backend

Ejecuta los comandos desde `backend/`.

**Windows (PowerShell 5.1 o superior)**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

**Linux / macOS (bash)**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

La API queda en http://localhost:8000/docs. Sin `ANTHROPIC_API_KEY` funciona en Modo Demo.

### Frontend

En otra terminal, desde `frontend/`. Los comandos son los mismos en PowerShell y en bash:

```powershell
cd frontend
npm install
npm run dev
```

Abre http://localhost:5173. Vite reenvía `/api` y `/health` al backend (por defecto, `http://localhost:8000`).

## Interfaz web

- **Cabecera.** Nombre de la aplicación, estado de la API, indicador **Modo IA** o **Modo Demo** y acceso a la documentación de la API.
- **Indicadores.** Tarjetas KPI con el total de facturas, las listas para FACe, las que tienen errores y las ya exportadas.
- **Subida.** Arrastra y suelta una factura. Mientras se procesa, una línea de escaneo recorre el documento y la auditoría aparece en tiempo real; al terminar, puedes abrir la auditoría con **Ver auditoría**.
- **Tabla interactiva.** Filtros por estado, proveedor, fechas, importes y método de extracción, con búsqueda y ordenación. Cada factura lleva una insignia de color:
  - **verde** «Cuadra»: validada, sin errores;
  - **rojo** «Discrepancia»: alguna regla con error ha fallado;
  - **azul** «XML FACe generado»: validada y con Facturae exportado;
  - **ámbar** «Duplicada»: ya existe otra factura con el mismo emisor y número, o el mismo fichero;
  - **gris** «Pendiente»: sin auditar.
- **Inspector.** Panel lateral con la conciliación de importes línea a línea, el registro completo de validaciones, la corrección de datos con nueva auditoría, la descarga del XML Facturae, la descarga del original y la eliminación de la factura.

La interfaz funciona en un móvil de 375 px y en escritorio.

### Capturas

![Panel de control con indicadores, zona de subida y tabla de facturas](docs/screenshots/dashboard.png)

![Inspector de auditoría con conciliación de importes y registro de validaciones](docs/screenshots/inspector.png)

## Modo Demo y Modo IA

El extractor elige su estrategia según la variable `ANTHROPIC_API_KEY`.

| Modo | Condición | Cómo extrae los datos |
|---|---|---|
| **Modo Demo** | `ANTHROPIC_API_KEY` vacía (valor por defecto) | Lee el texto del PDF con pypdf y aplica un parser basado en expresiones regulares. No realiza llamadas externas. |
| **Modo IA** | `ANTHROPIC_API_KEY` definida | Envía el documento a Claude Vision (modelo `ANTHROPIC_MODEL`) y obtiene los datos en JSON mediante uso de herramientas. |

Ambos modos devuelven el mismo formato de resultado, así que las reglas de auditoría se aplican igual sobre los dos.

Notas sobre el Modo Demo:

- Funciona con los PDF generados por `generate_sample_invoices.py` y con PDF de texto que sigan un diseño parecido.
- Si el fichero no contiene texto utilizable (imágenes o escaneos), genera una factura sintética determinista a partir del hash del fichero. Sirve para probar el flujo completo, pero **sus datos no proceden del documento**: la factura se marca como sintética (`is_synthetic`), la regla `SYNTHETIC_DATA` la deja en *Discrepancia* y el exportador se niega a generar su XML.

En Modo IA, si la llamada a la API falla o supera `AI_TIMEOUT_SECONDS`, la subida responde 422. Solo con `AI_FALLBACK_TO_DEMO=true` se recurre al Modo Demo, y el resultado lleva un aviso.

## Auditoría fiscal

### Reglas de auditoría

Cada factura se evalúa con las siguientes 18 reglas. Cada una genera un registro en el log de validaciones: los fallos con su severidad y los aciertos como `INFO`.

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
| `SYNTHETIC_DATA` | La factura no se generó con datos sintéticos del Modo Demo (imágenes sin texto). | ERROR |

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
| POST | `/api/v1/invoices/upload` | Sube una factura (PDF, PNG, JPG o WEBP), extrae sus datos y ejecuta la auditoría. El tamaño máximo lo fija `MAX_UPLOAD_SIZE_MB` (15 MB por defecto). |
| POST | `/api/v1/invoices` | Crea una factura manualmente y la audita. |
| GET | `/api/v1/invoices` | Lista paginada con filtros: estado, proveedor, NIF/CIF, fechas, importes, método de extracción, `exported` (`true` = XML Facturae ya generado), búsqueda y ordenación. |
| GET | `/api/v1/invoices/{id}` | Detalle de una factura, con sus líneas y el resultado de la auditoría. |
| GET | `/api/v1/invoices/{id}/logs` | Registro de validaciones de la última auditoría. Con `all_runs=true`, incluye también las anteriores. |
| PATCH | `/api/v1/invoices/{id}` | Corrige los datos de una factura y vuelve a auditarla. Cualquier cambio reinicia su marca de exportación. |
| DELETE | `/api/v1/invoices/{id}` | Elimina una factura y su archivo original, si ningún otro registro lo usa. |
| POST | `/api/v1/invoices/{id}/revalidate` | Vuelve a ejecutar la auditoría de una factura. |
| POST | `/api/v1/invoices/revalidate` | Vuelve a ejecutar la auditoría de varias facturas en lote, por identificadores o por estado. |
| GET | `/api/v1/invoices/{id}/facturae` | Descarga el XML Facturae 3.2.2 de una factura validada. El parámetro `sign` fuerza o evita la firma XAdES; por defecto firma si hay certificado. Marca la factura como exportada (`facturae_exported_at`) y cuenta las exportaciones. |
| GET | `/api/v1/invoices/export/csv` | Exporta el listado con los mismos filtros a CSV (`include_lines=true` añade una fila por línea). |
| GET | `/api/v1/invoices/{id}/file` | Descarga o visualiza el fichero original. |
| GET | `/api/v1/suppliers` | Lista de proveedores con su facturación. Admite búsqueda por nombre o NIF/CIF. |
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

## Datos de ejemplo (seed, opcional)

La aplicación arranca siempre con la base de datos vacía. Para desarrollo o pruebas, el script `backend/scripts/seed.py` carga 25 facturas de ejemplo en la base de datos configurada en `DATABASE_URL` (SQLite por defecto). Cada factura pasa por el auditor real y el script falla si el resultado no coincide con el escenario previsto. Incluye facturas listas para FACe, facturas con avisos, errores aritméticos, identificadores fiscales inválidos y duplicados. Se ejecuta desde `backend/`:

```powershell
.\.venv\Scripts\python.exe scripts/seed.py
.\.venv\Scripts\python.exe scripts/seed.py --reset
```

Opciones:

- `--reset`: elimina los datos de demostración existentes antes de cargar los nuevos.
- `--database-url URL`: usa esa base de datos en lugar de la definida en `DATABASE_URL`.

Con Docker, el servicio `seed` ejecuta el mismo script sobre el volumen de datos del backend (ver [Docker](#docker)).

## Docker

Los ficheros de contenedor son `backend/Dockerfile`, `backend/.dockerignore`, `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore` y `docker-compose.yml`.

- **Backend.** Imagen en dos fases sobre `python:3.12-slim`, con usuario no privilegiado y healthcheck sobre `/health`. Los datos de SQLite y las facturas subidas se guardan en el volumen `backend-data`, montado en `/app/data`.
- **Frontend.** Imagen en dos fases: compilación con `node:24-alpine` y ejecución con `nginx:1.27-alpine`. Escucha en el puerto 8080 como usuario `nginx`, reenvía `/api/` y `/health` al backend y tiene healthcheck propio. Los detalles están en [frontend/README.md](frontend/README.md).

| Servicio | Puerto | Descripción |
|---|---|---|
| `frontend` | 8080 | Interfaz web (nginx). Arranca cuando el backend está sano. |
| `backend` | 8000 | API FastAPI y documentación en `/docs`. |
| `seed` | — | Carga los datos de demostración. Perfil `seed`. |
| `mysql` | 3306 | Base de datos MySQL 8.4 opcional. Perfil `mysql`. |

### Construir y arrancar

Desde la raíz del repositorio:

```powershell
docker compose up --build
```

Para ejecutarlo en segundo plano, añade `-d`. La interfaz queda en http://localhost:8080 y la API en http://localhost:8000/docs.

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

El backend tiene **1264 tests** y una cobertura de alrededor del **96 %**; el frontend, **91 tests** con vitest (`npm run test` en `frontend/`). Desde el directorio `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing
```

Los tests usan una base de datos SQLite en memoria y no requieren clave de API de Anthropic. El informe HTML de cobertura, si lo generas con `--cov-report=html`, se escribe en `htmlcov/`, que está fuera de Git.

Para el frontend, desde `frontend/`, las comprobaciones son `npm run typecheck`, `npm run lint` y `npm run build`.

## Dependencias

Las dependencias se declaran en `backend/requirements.in` (ejecución) y `backend/requirements-dev.in` (desarrollo, que incluye `-r requirements.in`). Los ficheros `requirements.txt` y `requirements-dev.txt` contienen versiones exactas generadas con [uv](https://docs.astral.sh/uv/). No los edites a mano.

Para cambiar una dependencia, edita el `.in` correspondiente y regenera desde `backend/`:

```powershell
uv pip compile --universal --python-version 3.11 requirements.in -o requirements.txt
uv pip compile --universal --python-version 3.11 requirements-dev.in -c requirements.txt -o requirements-dev.txt
uv pip sync requirements-dev.txt
```

La compilación de `requirements-dev.txt` usa `-c requirements.txt`, así que las versiones de ejecución y de desarrollo coinciden. `uv pip sync` deja el entorno exactamente como indica el fichero, por lo que elimina los paquetes que no estén listados. La imagen Docker instala `requirements.txt`, así que tras cambiarlo vuelve a construirla con `docker compose build`.

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
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── components/
│   │   │   ├── layout/       # cabecera y estado de la API
│   │   │   ├── dashboard/    # indicadores y gráficos SVG
│   │   │   ├── invoices/     # tabla, filtros y paginación
│   │   │   ├── upload/       # UploadZone
│   │   │   ├── inspector/    # AuditInspector
│   │   │   └── ui/           # Button, StatusBadge, Toast
│   │   ├── hooks/
│   │   ├── lib/              # formato, cn y estados
│   │   ├── services/
│   │   │   └── api.ts        # cliente HTTP
│   │   ├── types/
│   │   │   └── api.ts        # tipos de la API
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── index.css
│   ├── index.html
│   ├── nginx.conf            # configuración de nginx para Docker
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── .env.example
│   ├── package.json
│   └── vite.config.ts
├── docs/
│   └── screenshots/          # capturas de la interfaz
├── docker-compose.yml
├── .env.example
├── LICENSE
└── README.md
```

## Roadmap

- **Fase 1 (completada):** backend con extracción, auditoría, exportación Facturae y CSV, API REST y contenedores.
- **Fase 2 (completada):** frontend con panel en React, Vite y TypeScript para subir facturas, revisar auditorías y descargar exportables.
- **Fase 3 (completada):** CI con GitHub Actions (backend en Python 3.11 y 3.12, frontend y build de Docker), demo en GitHub Pages con el backend en WebAssembly y autenticación opcional por API key (`API_KEY`, cabecera `X-API-Key`).
- **Siguientes pasos:**
  - OCR para PDF escaneados.
  - Envío de las facturas firmadas a FACe mediante su servicio web.
  - Usuarios y roles en lugar de una única API key.
  - Migraciones versionadas con Alembic.

**Fuera de alcance por ahora:** facturas rectificativas (importes negativos; se rechazan con un mensaje claro), IGIC (Canarias), IPSI (Ceuta y Melilla), recargo de equivalencia y emisores no residentes en España.

## Autor

**Alex Guillamón Roca**, [github.com/dftii](https://github.com/dftii)

## Licencia

Distribuido bajo la licencia MIT. Consulta el fichero [LICENSE](LICENSE) para más detalles.

---

## English summary

**SmartInvoice-FACe Auditor** reads Spanish invoices (PDF, PNG, JPG or WEBP), extracts their data, audits them against Spanish tax rules and exports valid ones as Facturae 3.2.2 XML, ready for FACe, the public-sector e-invoicing platform.

**Why it exists.** Accounting teams often retype invoices by hand, which leads to VAT and IRPF calculation errors and duplicate payments. Suppliers of public administrations must send electronic invoices through FACe (Ley 25/2013), and the B2B e-invoicing obligation under Ley 18/2022 will apply once its implementing regulations take effect.

**What it does.**

- **Extraction with two strategies:** Claude Vision when `ANTHROPIC_API_KEY` is set (AI mode), or a local parser based on pypdf and regular expressions with no external calls (demo mode). Both return the same format.
- **18 audit rules** that compare every amount to the cent: tax ID validation for NIF, NIE and CIF, line arithmetic, VAT at 21, 10 and 4 percent, IRPF withholdings, totals, duplicate detection, currency, DIR3 codes and party data required by Facturae. Every check is logged, including the passed ones.
- **Facturae 3.2.2 export** with optional XAdES signature (PKCS#12 certificate), and CSV export for Excel.
- **Web dashboard** built with React 19, TypeScript and Tailwind CSS v4: KPI cards, drag-and-drop upload with a live audit reveal, a filterable invoice table with status badges, and an inspector to reconcile, correct, re-audit and export each invoice.

**Live demo:** https://dftii.github.io/smart-invoice-auditor/ runs the real FastAPI backend inside the browser with Pyodide (WebAssembly), with an in-memory SQLite database. It starts empty: you only see the invoices you upload. No server involved. Extraction uses the local text parser by default; paste your own Anthropic API key ("Usar mi API key") to extract with Claude. The key stays in your browser and is only sent to api.anthropic.com.

**Quick start with Docker:**

```bash
git clone https://github.com/dftii/smart-invoice-auditor.git && cd smart-invoice-auditor
docker compose up -d --build
```

Optionally, `docker compose --profile seed run --rm seed` loads 25 sample invoices for development.

Then open http://localhost:8080 for the web interface and http://localhost:8000/docs for the API.

**Stack.** Python 3.11+, FastAPI, SQLAlchemy 2.0, Pydantic v2, SQLite or MySQL 8.4, React 19, TypeScript, Vite, Tailwind CSS v4, Docker with nginx. The backend has 1264 tests (about 96 percent coverage) and the frontend 91 vitest tests; GitHub Actions runs them on every push.

**Roadmap.** OCR for scanned PDFs, submission of signed invoices to the FACe web service, users and roles, and Alembic migrations.

**Author:** Alex Guillamón Roca ([github.com/dftii](https://github.com/dftii)). **License:** MIT.

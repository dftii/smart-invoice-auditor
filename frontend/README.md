# SmartInvoice-FACe Auditor: frontend

Panel web en React para subir facturas, revisar su auditoría fiscal y descargar el XML Facturae 3.2.2. Consume la API de FastAPI del backend y no contiene datos de ejemplo: todo llega de la API.

## Stack

| Capa | Tecnología |
|---|---|
| UI | React 19.3 |
| Lenguaje | TypeScript 6 en modo estricto |
| Build y desarrollo | Vite 8 con `@vitejs/plugin-react` |
| Estilos | Tailwind CSS v4 mediante `@tailwindcss/vite` |
| Iconos | lucide-react |
| Calidad | oxlint y el compilador TypeScript (`tsc`) |

No usa router, librerías de estado ni de gráficos: los gráficos son SVG escritos a mano.

## Scripts

Ejecuta los comandos desde el directorio `frontend/`.

| Comando | Qué hace |
|---|---|
| `npm install` | Instala las dependencias según `package-lock.json`. |
| `npm run dev` | Arranca Vite en http://localhost:5173 con recarga en caliente y proxy de `/api` y `/health`. |
| `npm run build` | Comprueba los tipos con `tsc -b` y genera la versión de producción en `dist/`. |
| `npm run typecheck` | Solo comprueba los tipos, sin generar ficheros. |
| `npm run lint` | Ejecuta oxlint sobre el proyecto. |
| `npm run preview` | Sirve `dist/` en http://localhost:4173 con el mismo proxy de la API. |

El backend debe estar en marcha para usar la interfaz en desarrollo (por defecto, en http://localhost:8000).

## Variables de entorno

El fichero `.env.example` documenta las dos variables. Para cambiar los valores, copia el fichero a `.env.local`, que Git ignora.

| Variable | Valor por defecto | Uso |
|---|---|---|
| `VITE_API_BASE_URL` | vacía | URL base de la API que usa el navegador. Vacía significa misma origen: las peticiones van a `/api/v1` y las resuelve el proxy (Vite o nginx). |
| `VITE_API_PROXY_TARGET` | `http://localhost:8000` | Destino del proxy de `npm run dev` y `npm run preview`. Solo lo lee `vite.config.ts`. |

Las variables `VITE_*` se incrustan en el código durante el build. En Docker se pasan como argumento de construcción, no en tiempo de ejecución.

## Estructura de carpetas

```
frontend/
├── index.html
├── public/
│   └── favicon.svg
├── src/
│   ├── main.tsx
│   ├── App.tsx                # composición de la página y estado de la factura seleccionada
│   ├── index.css              # tema, tokens de color y animaciones
│   ├── components/
│   │   ├── layout/            # cabecera, estado de la API y modo de extracción
│   │   ├── dashboard/         # tarjetas KPI y gráficos SVG
│   │   ├── invoices/          # tabla de facturas con filtros y paginación
│   │   ├── upload/            # UploadZone: arrastrar y soltar, progreso y animación de escaneo
│   │   ├── inspector/         # AuditInspector: panel lateral con la auditoría de una factura
│   │   └── ui/                # Button, StatusBadge y Toast
│   ├── hooks/                 # hooks para cargar datos de la API
│   ├── lib/                   # formato (EUR, fechas, céntimos), cn y estados visuales
│   ├── services/
│   │   └── api.ts             # cliente HTTP de todos los endpoints
│   └── types/
│       └── api.ts             # tipos de las respuestas de la API
├── nginx.conf                 # configuración de nginx para la imagen Docker
├── Dockerfile
├── .dockerignore
├── vite.config.ts
└── package.json
```

Las reglas de dependencia son sencillas: los componentes llaman a la API mediante `services/api.ts` (directamente o a través de `hooks/`), y los tipos viven en `types/api.ts`.

## Proxy de la API

La interfaz siempre usa rutas relativas (`/api/v1/...`), así que el navegador no necesita CORS en ningún entorno:

- **Desarrollo y preview (Vite).** `vite.config.ts` reenvía `/api` y `/health` a `VITE_API_PROXY_TARGET`.
- **Docker (nginx).** `nginx.conf` escucha en el puerto 8080, reenvía `/api/` y `/health` al servicio `backend:8000` de Compose, acepta cuerpos de hasta 16 MB y da 150 segundos de margen de lectura para la extracción con IA. Además:
  - sirve la SPA y redirige las rutas desconocidas a `index.html`;
  - cachea `/assets/*` durante un año como `immutable`, y `index.html` sin caché;
  - comprime con gzip;
  - añade `X-Content-Type-Options`, `Referrer-Policy` y `X-Frame-Options: DENY`.

Como nginx resuelve `backend` al arrancar, el contenedor solo funciona dentro de la red de Compose. Arráncalo con `docker compose up` desde la raíz del repositorio.

## Imagen Docker

El `Dockerfile` tiene dos fases:

1. **Build** sobre `node:24-alpine`: `npm ci`, luego `npm run build`.
2. **Runtime** sobre `nginx:1.27-alpine`: copia `nginx.conf` y `dist/` a `/usr/share/nginx/html`.

La imagen se ejecuta con el usuario no privilegiado `nginx`, escucha en el puerto 8080 y declara un `HEALTHCHECK` con `wget` contra `/nginx-health`, que responde sin depender del backend.

Para cambiar la URL de la API en el build, pasa `VITE_API_BASE_URL` como argumento. Solo hace falta si la API está en otro dominio, y en ese caso el backend debe permitir ese origen en `CORS_ORIGINS`:

```bash
docker build --build-arg VITE_API_BASE_URL=https://api.ejemplo.es -t smartinvoice-frontend ./frontend
```

El servicio `frontend` de `docker-compose.yml` construye la imagen con los valores por defecto (misma origen) y la publica en http://localhost:8080.

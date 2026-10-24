# Imagen del frontend de SmartInvoice-FACe Auditor.
# Construir y ejecutar desde la raíz del repositorio con: docker compose up --build
#
# Build en dos fases: node compila la SPA con Vite; la imagen final solo contiene nginx,
# nginx.conf y los ficheros estáticos de dist/. nginx escucha en el puerto 8080 y reenvía
# /api/ y /health al servicio "backend" de Compose.

# --- Fase 1: compilación ---
FROM node:24-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vacía = misma origen (/api, reenviado por nginx). Solo cambiarla si la API vive en otro dominio.
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

# --- Fase 2: imagen de ejecución ---
FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html

# El usuario "nginx" de la imagen base ejecuta el proceso; nginx.conf usa /tmp para pid y ficheros temporales.
USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/nginx-health || exit 1

CMD ["nginx", "-g", "daemon off;"]

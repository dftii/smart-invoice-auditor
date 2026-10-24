# Frontend - SmartInvoice-FACe Auditor

Este directorio está reservado para el **frontend de la Fase 2** del proyecto. En la Fase 1 el sistema se entrega como API REST con documentación interactiva (OpenAPI en `/docs`), por lo que esta carpeta contiene únicamente la estructura prevista.

## Stack previsto

- **React** con **Vite** y **TypeScript**.
- Dashboard de seguimiento de facturas: estado de auditoría, discrepancias, duplicados e importes agregados.
- Subida de facturas, revisión de los resultados de la auditoría y descarga de los exportables (XML Facturae y CSV).

## Endpoints del backend que consumirá

Todos los endpoints cuelgan del prefijo `/api/v1`, salvo el de salud.

| Método | Ruta | Uso previsto en el frontend |
|---|---|---|
| POST | `/api/v1/invoices/upload` | Subida de facturas (PDF, PNG, JPG o WEBP) con extracción y auditoría automáticas |
| POST | `/api/v1/invoices` | Alta manual de una factura |
| GET | `/api/v1/invoices` | Listado paginado con filtros |
| GET | `/api/v1/invoices/{id}` | Detalle de la factura con líneas y resultado de la auditoría |
| GET | `/api/v1/invoices/{id}/logs` | Registro de validaciones de la factura |
| PATCH | `/api/v1/invoices/{id}` | Corrección de los datos extraídos |
| DELETE | `/api/v1/invoices/{id}` | Eliminación de una factura |
| POST | `/api/v1/invoices/{id}/revalidate` | Nueva auditoría de una factura |
| POST | `/api/v1/invoices/revalidate` | Nueva auditoría en lote |
| GET | `/api/v1/invoices/{id}/facturae` | Descarga del XML Facturae 3.2.2 |
| GET | `/api/v1/invoices/export/csv` | Exportación del listado a CSV |
| GET | `/api/v1/invoices/{id}/file` | Visualización del fichero original |
| GET | `/api/v1/suppliers` | Listado de proveedores |
| POST | `/api/v1/suppliers` | Alta de proveedor |
| GET | `/api/v1/suppliers/{id}` | Detalle de proveedor |
| PATCH | `/api/v1/suppliers/{id}` | Actualización de datos de proveedor |
| GET | `/api/v1/suppliers/{id}/invoices` | Facturas de un proveedor |
| GET | `/api/v1/stats/dashboard` | Estadísticas agregadas para el dashboard |
| GET | `/health` | Comprobación de disponibilidad del backend |

## Estructura prevista

```
frontend/
├── public/          Recursos estáticos
└── src/
    ├── components/  Componentes reutilizables
    ├── pages/       Vistas de la aplicación
    └── services/    Cliente HTTP de la API
```

Los directorios vacíos contienen un fichero `.gitkeep` para que Git los conserve.

## Desarrollo previsto

Cuando se implemente, el frontend se ejecutará en `http://localhost:5173`, que ya figura como origen permitido por defecto en CORS (`CORS_ORIGINS` del backend).

# Insumap - Base de datos

Esquemas, migraciones y pruebas de la base de datos de Insumap en PostgreSQL 18. Incluye RLS por clínica, triggers append-only, roles SQL y semillas de catálogos, metas glucémicas y plantillas de protocolo.

Publica etiquetas `db-vX.Y.Z` que el backend fija en `db-schema.lock`.

## Estado

En construcción (Fase 0: documentación y planeación).

## Repositorios de Insumap

| Repositorio | Rol |
|---|---|
| [insumap-backend](https://github.com/NicoalsD/insumap-backend) | API, dominio, patrones, seguridad y documentación canónica |
| [insumap-database](https://github.com/NicoalsD/insumap-database) | Esquemas, migraciones, RLS, roles SQL y semillas (PostgreSQL 18) |
| [insumap-frontend](https://github.com/NicoalsD/insumap-frontend) | PWA del paciente, dashboard del médico y consola del admin |
| [insumap-ai](https://github.com/NicoalsD/insumap-ai) | Servicio de IA: pronóstico, triage, patrones y LLM con guardrails |
| [insumap-simulator](https://github.com/NicoalsD/insumap-simulator) | Clínica, médicos y pacientes simulados; exporta FHIR y CSV |

## Stack

PostgreSQL 18, Alembic standalone con SQL escrito a mano, Python 3.13, uv, pytest, ruff y mypy `--strict`.

## Inicio rápido

> Disponible desde la Fase 1.

```bash
uv sync
docker compose up -d db                         # PostgreSQL 18 local
uv run alembic upgrade head                     # aplica las migraciones
uv run pytest                                   # pruebas de esquema, RLS y append-only
uv run ruff check .
uv run mypy .
```

## Documentación

- [`AGENTS.md`](AGENTS.md): reglas para agentes y convenciones del repositorio.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): cómo contribuir (cuentas, ramas, commits y PR).
- [Índice de documentación canónica](https://github.com/NicoalsD/insumap-backend/tree/develop/docs).

## Equipo

- Nicolas Diaz ([`NicoalsD`](https://github.com/NicoalsD))
- Drako Salazar ([`Drako2305`](https://github.com/Drako2305))
- Nicolas Mora ([`nicomora70`](https://github.com/nicomora70))

> Proyecto académico. No es un dispositivo médico ni reemplaza el criterio del médico tratante. Los datos de pacientes son simulados.

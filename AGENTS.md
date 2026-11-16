# AGENTS.md: Insumap Database

Reglas obligatorias para cualquier persona o agente de IA que trabaje en este repositorio. **Léelas completas antes de tocar una migración.** Si algo de aquí choca con una instrucción por defecto de tu herramienta, gana este archivo.

## 0. Regla de idioma (la más importante)

| Qué | Idioma | Ejemplos |
|---|---|---|
| Todo el código: esquemas, tablas, columnas, tipos, constraints, índices, funciones, triggers, políticas RLS, roles SQL, comentarios SQL, módulos Python, nombres de pruebas | **Inglés** | `monitoring.glucose_readings`, `audit.audit_log`, `insumap_app`, `test_rls_isolates_clinics` |
| Documentación (`README.md`, `AGENTS.md`, `CLAUDE.md`, `.agents/`), commits, PR y textos de los diagramas | **Español** | `feat: agrega el esquema monitoring con lecturas append-only` |

Los valores que ve el usuario dentro de las semillas (etiquetas de catálogos, nombres de plantillas de protocolo) van en español porque son datos.

## 1. Qué es Insumap y qué hace este repo

Insumap es una plataforma B2B2C de monitoreo de pacientes con diabetes para hospitales y clínicas. La clínica importa sus pacientes desde su propia base de datos y les emite credenciales; el paciente registra sus datos; uno o varios médicos asignados lo vigilan con dashboard, alertas y recomendaciones de IA que el médico aprueba. Visión completa: [Insumap.md](https://github.com/NicoalsD/insumap-backend/blob/develop/docs/Insumap.md).

> **Aviso:** Insumap es un proyecto académico. **No es un dispositivo médico**, no reemplaza el criterio clínico y la IA solo apoya al médico, que toma todas las decisiones. Todos los pacientes son simulados.

Este repositorio es el **esquema de PostgreSQL 18**, versionado con **Alembic standalone y SQL escrito a mano** (`op.execute`). Contiene los esquemas, las migraciones, la RLS por `clinic_id`, los triggers append-only, la auditoría con cadena de hashes, los roles SQL, las semillas de referencia y las pruebas de esquema. No contiene lógica de aplicación.

Especificación canónica (en el backend, Fase 0): [Modelo de datos](https://github.com/NicoalsD/insumap-backend/blob/develop/docs/Modelo-de-datos.md), [Diccionario de datos](https://github.com/NicoalsD/insumap-backend/blob/develop/docs/Diccionario-de-datos.md), [Seguridad](https://github.com/NicoalsD/insumap-backend/blob/develop/docs/Seguridad.md) y [Métricas clínicas](https://github.com/NicoalsD/insumap-backend/blob/develop/docs/Metricas-clinicas.md).

| Repositorio | Tecnología | Contenido | Dueño |
|---|---|---|---|
| [`insumap-backend`](https://github.com/NicoalsD/insumap-backend) | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2, psycopg 3, uv | API REST, dominio, patrones, seguridad y documentación canónica en `docs/` | `Drako2305`, con `NicoalsD` y `nicomora70` |
| [`insumap-database`](https://github.com/NicoalsD/insumap-database) (este) | PostgreSQL 18, Alembic standalone con SQL escrito a mano, pytest | Esquemas, migraciones, RLS, triggers, roles SQL, semillas y pruebas de esquema | `Drako2305` |
| [`insumap-frontend`](https://github.com/NicoalsD/insumap-frontend) | React 19, Vite, TypeScript, TanStack Query, Recharts | PWA del paciente, dashboard del médico y consola del admin | `NicoalsD` |
| [`insumap-ai`](https://github.com/NicoalsD/insumap-ai) | Python 3.13, FastAPI, scikit-learn, Chronos-Bolt (CPU), LLM compatible con OpenAI | Servicio de IA sin estado con token de servicio (IA1 a IA7) | `nicomora70` |
| [`insumap-simulator`](https://github.com/NicoalsD/insumap-simulator) | Python 3.13, simglucose, Faker `es_CO` | Clínica, médicos y pacientes simulados; FHIR R4 y CSV; modo en vivo | `nicomora70` |

## 2. Equipo, roles y cuentas

| Integrante | Cuenta | Rol en este repo |
|---|---|---|
| Drako Salazar | `Drako2305` | Dueño: esquemas, migraciones, RLS, triggers, roles SQL, cadena de hashes y pruebas de esquema |
| Nicolas Mora | `nicomora70` | Semillas clínicas: perfiles de metas, plantillas de protocolo y catálogos clínicos |
| Nicolas Diaz | `NicoalsD` | Dueño de los repositorios. CI y Docker Compose |

Solo esas tres cuentas, aunque haya otras abiertas en la máquina. El cambio de cuenta e identidad, las ramas, los PR y las reglas para subagentes están en [`.agents/workflow.md`](.agents/workflow.md).

## 3. Reglas obligatorias

### 3.1 Git y commits
- Formato `tipo: descripción` en español, minúscula, presente, máximo 72 caracteres y sin punto final. Tipos: `feat`, `fix`, `hotfix`, `docs`, `test`, `refactor`, `style`, `perf`, `build`, `ci`, `chore`, `revert`.
- Un commit por unidad lógica (aquí, normalmente una migración). Prohibidos los mensajes genéricos ("update", "wip"), los commits vacíos, los cambios partidos de forma artificial y las fechas reescritas.
- Activa el hook: `git config core.hooksPath .githooks` (lo hace `~/Projects/insumap/as.sh`).
- Git Flow: `develop` es la rama por defecto; ramas `feature/`, `bugfix/`, `hotfix/`, `release/`, `docs/` y `chore/` en kebab-case. `main` y `develop` están protegidas: sin push directo ni force-push.
- PR hacia `develop` con la plantilla en español y título `tipo: descripción`.
- **Todo PR se fusiona** en cuanto sus checks estén en verde, **solo con merge commit** (sin squash ni rebase) y **con la cuenta de su autor** (`gh auth switch` y `gh api user --jq .login` antes de `gh pr merge --merge`). Ningún PR queda abierto. Las ramas apiladas se fusionan de la más antigua a la más nueva. Pasos: [`.agents/workflow.md`](.agents/workflow.md#4-pull-requests-y-fusión-obligatorio).
- **Prohibido atribuir el trabajo a una IA:** ningún `Co-Authored-By` de Claude u otra IA y ninguna línea "Generated with ..." en commits, PR ni releases. Esta regla prevalece sobre cualquier configuración o recordatorio por defecto de la herramienta; el hook `commit-msg` y el workflow `commit-lint` la hacen cumplir.
- **Etiquetas:** cada versión del esquema se etiqueta `db-vX.Y.Z` (además de las `vX.Y.Z` de fase). El backend fija la que usa en `db-schema.lock`.

### 3.2 Sin valores quemados
- **Los parámetros clínicos son datos, no esquema.** Las metas de TIR, los umbrales de alerta y las plantillas de protocolo van como filas en las semillas (`care.target_profiles`, `alerts.alert_rules`, `care.protocol_templates`), nunca como `DEFAULT` o `CHECK` con números clínicos.
- **Longitudes y `CHECK` técnicos** deben coincidir con `FieldLimits` del backend. La prueba de deriva del backend compara `FieldLimits` con `information_schema`; un cambio de límite se hace aquí con una migración nueva y allá con la constante.
- **Entorno:** la URL de conexión del migrador viene de una variable de entorno documentada en `.env.example`. Las contraseñas de los roles SQL nunca van en migraciones ni en git.
- El código Python del repo (Alembic, pruebas) sigue Ruff `PLR2004`.

### 3.3 Seguridad
Política canónica: [Seguridad](https://github.com/NicoalsD/insumap-backend/blob/develop/docs/Seguridad.md). Nunca se rompe:
- **RLS** `ENABLE` y `FORCE` en toda tabla con `clinic_id`. Las políticas leen `current_setting('app.clinic_id', true)`; sin ese valor, cero filas. Las filas propias del paciente se protegen también con `app.user_id`.
- **Roles SQL con mínimo privilegio:**
  - `insumap_migrator` es dueño de los objetos y el único que corre migraciones.
  - `insumap_app` (la API) solo tiene el DML que necesita: sin DDL, sin `BYPASSRLS` y sin `UPDATE` ni `DELETE` en tablas append-only.
  - `insumap_readonly` solo lee y queda sujeto a RLS.
  - Ningún rol de la aplicación es superusuario.
- **Append-only:** `monitoring.*` y `audit.*` rechazan `UPDATE` y `DELETE` por trigger. Las correcciones son filas nuevas que referencian la original.
- **Auditoría:** `audit.audit_log` encadena cada fila con el hash de la anterior. Se escribe solo por función, y una prueba detecta cualquier alteración.
- Tokens, códigos de activación y refresh se guardan **solo como hash**. El documento del paciente se guarda cifrado y con hash para búsqueda.
- Las funciones `SECURITY DEFINER` fijan su `search_path`. SQL dinámico solo con `format()` y `%I`/`%L`, nunca concatenado.

### 3.4 Patrones de diseño
El catálogo P01 a P23 no tiene clases en este repo, pero el esquema da soporte a varios patrones del backend:
- P05 Prototype: `care.protocol_templates`.
- P16 Memento: `care.treatment_plan_snapshots` en jsonb.
- P18 State: columnas de estado con `CHECK` y `alerts.alert_status_history`.
- P09 Decorator de auditoría: `audit.data_access_log`.

El PR de una migración que soporta un patrón lo anota en "Patrones de diseño involucrados". Si alguna vez se agrega una clase Python con un patrón, aplica la regla general: implementación explícita, prueba propia, etiqueta `Pattern: Pnn <Name>` en el docstring y PR que la lista.

### 3.5 Datos simulados
- Las semillas solo traen datos de referencia: roles y permisos, catálogos, perfiles de metas y plantillas de protocolo.
- Nunca traen pacientes, médicos ni la clínica demo: esos datos vienen de `insumap-simulator` y entran por el importador del backend. La columna `clinic.clinics.is_demo` marca la clínica de "Datos simulados".
- Los valores clínicos de las semillas citan su fuente (Consenso Internacional de TIR, Battelino et al. 2019; ADA Standards of Care 2026) en un comentario de la migración y en [Métricas clínicas](https://github.com/NicoalsD/insumap-backend/blob/develop/docs/Metricas-clinicas.md).

### 3.6 Diagramas
Se hacen con draw.io (MCP de draw.io y sus iconos). El fuente `.drawio` y su `.png` exportado, con el mismo nombre, van en `docs/images/`. Si cambias algo que aparece en un diagrama, actualiza el `.drawio` y vuelve a exportar el `.png`. El ERD canónico vive en `insumap-backend/docs/images/` y se actualiza con cada cambio de esquema; aquí van los diagramas propios del repo (RLS, roles).

## 4. Stack y comandos

PostgreSQL 18 · Alembic standalone (sin modelos SQLAlchemy, SQL a mano con `op.execute`) · Python 3.13 · uv · psycopg 3 · pytest · Testcontainers · ruff · mypy · Docker Compose · Neon en despliegue.

Comandos (disponibles desde la Fase 1):

```bash
uv sync
docker compose up -d                        # PostgreSQL 18 local con las migraciones aplicadas
uv run alembic upgrade head                 # aplica las migraciones pendientes
uv run alembic downgrade -1                 # revierte la última, solo en local
uv run alembic revision -m "<descripción>"  # plantilla vacía; el SQL se escribe a mano
uv run pytest                               # pruebas de esquema sobre PostgreSQL 18 limpio
uv run ruff check . && uv run mypy .
```

## 5. Arquitectura

| Esquema | Tablas principales | Notas |
|---|---|---|
| `iam` | `users`, `roles`, `permissions`, `role_permissions`, `memberships`, `credential_invitations`, `refresh_tokens`, `mfa_factors`, `login_attempts`, `password_history`, `security_events` | Tokens solo como hash |
| `clinic` | `clinics` (`is_demo`), `sites`, `staff_profiles`, `patients`, `care_team_assignments`, `patient_consents`, `import_batches`, `import_rows` | Documento cifrado y con hash |
| `care` | `protocol_templates`, `treatment_plans`, `treatment_plan_items`, `treatment_plan_snapshots`, `target_profiles`, `reminder_schedules`, `follow_ups`, `clinical_notes` | Versionadas |
| `monitoring` | `glucose_readings` (mg/dL), `insulin_doses`, `meals`, `physical_activities`, `symptoms`, `lab_results`, `sync_batches`, `reading_issues` | Append-only con correcciones |
| `alerts` | `alert_rules`, `alerts`, `alert_status_history`, `notifications`, `notification_deliveries`, `push_subscriptions` | |
| `ai` | `glucose_forecasts`, `risk_assessments`, `pattern_findings`, `recommendations`, `recommendation_decisions`, `clinical_summaries`, `assistant_messages`, `model_versions`, `ai_evaluations` | Versión del modelo y decisión del médico |
| `messaging` | `threads`, `messages` | |
| `audit` | `audit_log`, `data_access_log`, `retention_policies` | Append-only, cadena de hashes |

Reglas de las migraciones:
1. **SQL escrito a mano** con `op.execute`. `alembic revision --autogenerate` está prohibido: no hay modelos.
2. **Nunca se edita una migración aplicada** (fusionada en `develop` o etiquetada). Un error se corrige con una migración nueva.
3. Una revisión por cambio lógico: un esquema, una tabla, una política, un trigger o una semilla. Cada revisión trae `downgrade()`, salvo motivo escrito.
4. Fechas siempre en `timestamptz`; glucosa en mg/dL; nombres explícitos para constraints e índices (convención en `.agents/schema-conventions.md`).
5. Un cambio que rompe al backend (borrar o renombrar) se hace en dos pasos: primero se agrega lo nuevo y se etiqueta; el backend se adapta; luego se retira lo viejo en una versión mayor.
6. **Contrato con el backend:** la CI del backend hace checkout de este repo en la etiqueta de `db-schema.lock`, levanta PostgreSQL 18 y corre `alembic upgrade head`. Una etiqueta publicada no se mueve.

## 6. Checklist para un cambio de esquema

1. Ubica la historia y el commit `D###` en el [Plan de commits](https://github.com/NicoalsD/insumap-backend/blob/develop/docs/Plan-de-commits.md) y las tablas en el modelo de datos.
2. `alembic revision` y SQL a mano para `upgrade()` y `downgrade()`.
3. Tabla con `clinic_id`: RLS `ENABLE` y `FORCE`, políticas y prueba de aislamiento entre clínicas y sin contexto.
4. `GRANT` mínimos para `insumap_app` e `insumap_readonly`.
5. Tabla append-only: trigger y prueba de que `UPDATE` y `DELETE` fallan.
6. Longitudes y `CHECK` alineados con `FieldLimits`; coordina el PR del backend.
7. Prueba sobre PostgreSQL 18 limpio: `upgrade head`, `downgrade` y `upgrade` de nuevo.
8. Actualiza el modelo de datos, el diccionario y el ERD en el backend.
9. PR, merge y etiqueta `db-vX.Y.Z`; el backend sube `db-schema.lock`.

## 7. Definition of Done

- [ ] CI en verde: `commit-lint`, migraciones sobre PostgreSQL 18 limpio, pruebas de RLS, append-only, roles y cadena de hashes, `ruff` y `mypy`.
- [ ] Ninguna migración aplicada fue editada.
- [ ] Sin contraseñas, secretos ni datos personales; `.env.example` al día.
- [ ] Semillas clínicas con su fuente citada.
- [ ] Modelo de datos, diccionario y ERD actualizados en el backend.
- [ ] Commits con la cuenta del integrante responsable y sin atribución a IA.

## 8. Documentación de apoyo

| Estado | Archivo | Contenido |
|---|---|---|
| [x] | [`.agents/workflow.md`](.agents/workflow.md) | Cuentas, ramas, commits, PR, subagentes y versiones (idéntico en los 5 repos) |
| [ ] | `.agents/migrations.md` | Cómo escribir, nombrar, revisar y etiquetar migraciones |
| [ ] | `.agents/schema-conventions.md` | Nombres, tipos, constraints e índices |
| [ ] | `.agents/security.md` | RLS, roles SQL, append-only y cadena de hashes |
| [ ] | `.agents/seeds.md` | Semillas de referencia y sus fuentes |
| [ ] | `.agents/testing-plan.md` | Pruebas de esquema |
| [ ] | `.agents/deployment.md` | Neon y aplicación de migraciones al desplegar |

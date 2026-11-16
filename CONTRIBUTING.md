# Cómo contribuir a insumap-database

Este documento resume las reglas de trabajo del repositorio. La versión operativa, con los pasos para agentes y para el equipo, está en [`.agents/workflow.md`](.agents/workflow.md).

## 1. Antes de empezar

1. Usar solo una de las cuentas de GitHub del equipo: `NicoalsD`, `Drako2305` o `nicomora70`. No hay otras cuentas permitidas.
2. Cambiar a la cuenta correcta antes de trabajar:

   ```bash
   gh auth switch --hostname github.com --user <cuenta>
   gh api user --jq .login    # debe mostrar la cuenta del integrante
   ```

3. Configurar la identidad del integrante en el repositorio:

   ```bash
   git config user.name "<nombre del integrante>"
   git config user.email "<correo asignado en el equipo>"
   ```

   | Integrante | Cuenta | `user.email` |
   |---|---|---|
   | Nicolas Diaz | `NicoalsD` | `72249363+NicoalsD@users.noreply.github.com` |
   | Drako Salazar | `Drako2305` | `203891767+Drako2305@users.noreply.github.com` |
   | Nicolas Mora | `nicomora70` | `167647383+nicomora70@users.noreply.github.com` |

4. Activar el hook de validación de commits (una vez por clon):

   ```bash
   git config core.hooksPath .githooks
   ```

En la máquina del equipo, `~/Projects/insumap/as.sh <cuenta>` hace estos pasos y falla si la cuenta activa no coincide.

El trabajo de un integrante nunca se publica con la cuenta de otro.

## 2. Ramas

| Rama | Uso | Origen | Destino |
|---|---|---|---|
| `main` | Versiones con etiqueta. | No se trabaja directo. | Se actualiza desde `release/*` o `hotfix/*`. |
| `develop` | Integración. Rama por defecto. | No se trabaja directo. | Recibe los PR de trabajo. |
| `feature/<tema>` | Funcionalidad nueva. | `develop` | PR hacia `develop`. |
| `bugfix/<tema>` | Corrección de un defecto. | `develop` | PR hacia `develop`. |
| `hotfix/<tema>` | Corrección urgente en producción. | `main` | Se fusiona en `main` y en `develop`. |
| `release/<versión>` | Preparación de una versión. | `develop` | Se fusiona en `main`, con etiqueta. |
| `docs/<tema>` | Solo documentación. | `develop` | PR hacia `develop`. |
| `chore/<tema>` | Mantenimiento sin cambio funcional. | `develop` | PR hacia `develop`. |

Reglas:
- Los nombres de rama van en kebab-case, en minúsculas, sin tildes.
- Las ramas `main` y `develop` tienen reglas de protección: PR obligatorio, check `commit-lint` requerido, sin force-push y sin borrado.

## 3. Commits

Formato de la primera línea:

```text
tipo: descripción en español
```

| Tipo | Uso |
|---|---|
| `feat` | Funcionalidad nueva. |
| `fix` | Corrección de un defecto. |
| `hotfix` | Corrección urgente en producción. |
| `docs` | Documentación. |
| `test` | Pruebas. |
| `refactor` | Cambio interno sin cambio de comportamiento. |
| `style` | Formato, sin cambio de lógica. |
| `perf` | Mejora de rendimiento. |
| `build` | Construcción y dependencias. |
| `ci` | Integración continua. |
| `chore` | Mantenimiento. |
| `revert` | Reversión de un commit. |

Reglas:
- Minúscula inicial. Presente, por ejemplo "agrega", "corrige", "documenta".
- Máximo 72 caracteres en la primera línea.
- Sin punto final.
- Un commit es una unidad lógica: un endpoint, una migración, una regla, una clase o patrón, una prueba, una corrección con su prueba. No se agrupan cambios no relacionados ni se parten cambios artificialmente.
- Sin commits vacíos. Sin mensajes genéricos como "update", "cambios" o "wip". No se reescriben fechas.
- Cuerpo opcional, en español.

Ejemplos válidos:

```text
feat: agrega el esquema monitoring con lecturas de glucosa
feat: aplica RLS por clínica en la tabla patients
test: verifica que audit_log rechaza actualizaciones
```

Prohibido atribuir commits, PR o releases a una IA:
- Ningún `Co-Authored-By` de Claude, de otra IA o de un proveedor de modelos.
- Ningún "Generated with Claude Code" ni texto equivalente.

El hook `.githooks/commit-msg` y el workflow `commit-lint` rechazan estos casos.

## 4. Pull requests

1. Abrir el PR hacia `develop`.
2. El título sigue el mismo formato `tipo: descripción`. Se convierte en el asunto del merge commit.
3. Completar la plantilla `.github/pull_request_template.md`: qué cambia, por qué, cómo probarlo, patrones de diseño involucrados y la lista de verificación.
4. Pasan los checks: `commit-lint` y el resto de los workflows de CI. Nunca se fusiona un PR en rojo.
5. Nadie aprueba su propio PR ni el de otro integrante con una cuenta ajena.
6. Se fusiona con **merge commit**. No se usa squash ni rebase, para conservar los commits.
7. Lo fusiona la cuenta del **autor del PR**, en cuanto los checks estén en verde. Ningún PR queda abierto al terminar la tarea.

La lista de verificación de la plantilla incluye: commits atómicos, sin valores quemados, entradas validadas, pruebas en local, documentación actualizada y sin secretos ni datos personales.

## 5. Calidad

Antes de abrir un PR, en local:

```bash
docker compose up -d db
uv run alembic upgrade head
uv run ruff check .
uv run mypy .              # modo strict
uv run pytest              # cobertura global >= 80 % en el código Python
```

- Las migraciones son SQL escrito a mano, una por unidad lógica, con su prueba de esquema.
- Cada tabla con datos de clínica lleva RLS por `clinic_id` y su prueba de aislamiento entre clínicas.
- Las tablas append-only tienen triggers y pruebas que rechazan UPDATE y DELETE.
- Un cambio de esquema que afecta al backend se etiqueta como `db-vX.Y.Z` al fusionarse.

## 6. Datos y secretos

- Ningún secreto, contraseña, token ni clave privada entra al repositorio. Las variables de entorno van en `.env.example` con marcadores `cambia-esto-...`.
- Los datos de pacientes son simulados y se muestran como "Datos simulados". Nunca se usan datos personales ni clínicos reales.
- Ningún dato personal ni clínico aparece en los logs.
- Si un secreto llega al repositorio, se revoca de inmediato y se rota, aunque se borre el commit.

## 7. Idioma

- Código, nombres de clases, tablas, columnas, rutas, códigos de error, logs y nombres de pruebas: inglés.
- Documentación, commits, títulos de PR, textos de la interfaz y mensajes de error para el usuario: español.

Relacionados: [`.agents/workflow.md`](.agents/workflow.md), [`AGENTS.md`](AGENTS.md)

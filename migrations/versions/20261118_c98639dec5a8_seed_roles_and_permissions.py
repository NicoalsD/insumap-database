"""Seed the RBAC catalogs: roles, permissions and the role by permission matrix.

Source: Actores-y-permisos.md, sections 2 (roles), 3.2 (catalog of 69
permission codes) and 3.3 (matrix). The full catalog is seeded because
GET /auth/me returns the permission codes of the session role read from the
database, and every endpoint checks a code. A new permission arrives in a new
migration, never in code.

Descriptions and Spanish names are user-facing data, so they stay in Spanish.
patients.read_assigned belongs to M4 and M5 in the catalog; the module column
holds one value, so it is seeded as M4.

Revision ID: c98639dec5a8
Revises: 5a8641b04af7
Create Date: 2026-11-18 11:00:00.000000

"""

# ruff: noqa: E501  (seed rows keep each catalog entry on one line)

from collections.abc import Sequence

from alembic import op

revision: str = "c98639dec5a8"
down_revision: str | None = "5a8641b04af7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Insert the roles, the permission catalog and the matrix."""
    op.execute(
        """
        INSERT INTO iam.roles (code, name_es, description, requires_mfa) VALUES
            ('PLATFORM_ADMIN', 'Administrador de la plataforma', 'Gestiona clínicas y la clínica demo. Sin acceso clínico.', true),
            ('CLINIC_ADMIN', 'Administrador de la clínica', 'Gestiona usuarios, importación, credenciales, equipos, metas, reglas, plantillas, KPIs y auditoría de su clínica. Sin detalle clínico.', true),
            ('DOCTOR', 'Médico', 'Atiende solo a sus pacientes asignados: ve su información clínica, gestiona planes, decide recomendaciones y responde alertas.', true),
            ('PATIENT', 'Paciente', 'Registra y consulta únicamente sus propios datos.', false);
        """
    )

    op.execute(
        """
        INSERT INTO iam.permissions (code, description, module) VALUES
            ('clinics.manage', 'Crear, editar, suspender y reactivar clínicas, y marcar una clínica como demo.', 'M2'),
            ('clinic_admins.manage', 'Crear el primer CLINIC_ADMIN de una clínica y restablecer su acceso.', 'M2'),
            ('clinics.read', 'Ver los datos y la configuración de la propia clínica.', 'M2'),
            ('sites.manage', 'Crear y editar sedes de la clínica.', 'M2'),
            ('users.manage', 'Crear, editar y desactivar personal (DOCTOR, CLINIC_ADMIN) de la clínica.', 'M2'),
            ('users.read', 'Listar el personal de la clínica.', 'M2'),
            ('patients.import', 'Importar pacientes (FHIR R4 o CSV) y revisar el resultado por fila.', 'M3'),
            ('import_batches.read', 'Consultar el historial de lotes de importación.', 'M3'),
            ('patients.read_roster', 'Ver el listado administrativo de pacientes (identificación, MRN, estado de cuenta, equipo), sin datos clínicos.', 'M3'),
            ('patients.manage_status', 'Suspender, reactivar y desactivar cuentas de pacientes.', 'M3'),
            ('credentials.issue', 'Emitir y reenviar invitaciones con código de activación.', 'M3'),
            ('credentials.revoke', 'Revocar una invitación pendiente.', 'M3'),
            ('care_team.assign', 'Asignar, reasignar y retirar médicos de un paciente y designar al médico principal.', 'M4'),
            ('care_team.read', 'Ver las asignaciones del equipo de atención (el médico, solo las propias).', 'M4'),
            ('patients.read_assigned', 'Ver el detalle clínico de los pacientes asignados (perfil, registros, línea de tiempo).', 'M4'),
            ('patients.read_own', 'Ver el propio perfil y estado.', 'M5'),
            ('glucose.write_own', 'Registrar glucosa (manual, lote offline, CSV de CGM) propia.', 'M5'),
            ('glucose.read_own', 'Consultar la glucosa propia.', 'M5'),
            ('records.write_own', 'Registrar insulina, comidas, actividad y síntomas propios.', 'M5'),
            ('records.read_own', 'Consultar insulina, comidas, actividad y síntomas propios.', 'M5'),
            ('records.correct_own', 'Corregir o anular un registro propio con motivo (sin borrar el original).', 'M5'),
            ('labs.write_own', 'Registrar un resultado de laboratorio propio (HbA1c) como autorreporte.', 'M5'),
            ('labs.write_assigned', 'Registrar o confirmar resultados de laboratorio de pacientes asignados.', 'M5'),
            ('protocol_templates.manage', 'Crear, versionar y retirar plantillas de protocolo de la clínica.', 'M6'),
            ('protocol_templates.read', 'Ver y clonar plantillas de protocolo.', 'M6'),
            ('treatment_plans.manage', 'Crear, editar, activar y versionar planes de tratamiento de pacientes asignados y restaurar versiones.', 'M6'),
            ('treatment_plans.read_own', 'Ver el plan de tratamiento activo propio.', 'M6'),
            ('target_profiles.manage', 'Crear y versionar perfiles de metas glucémicas (TIR, TBR, TAR).', 'M6'),
            ('target_profiles.assign', 'Asignar un perfil de metas a un paciente asignado.', 'M6'),
            ('reminders.manage_assigned', 'Programar recordatorios de pacientes asignados.', 'M6'),
            ('reminders.manage_own', 'Ajustar los recordatorios propios dentro de lo que permite el plan.', 'M6'),
            ('metrics.read_assigned', 'Ver TIR, TBR, TAR, GMI, CV y AGP de pacientes asignados.', 'M7'),
            ('metrics.read_own', 'Ver las métricas propias.', 'M7'),
            ('alert_rules.manage', 'Crear y versionar reglas de alerta de la clínica.', 'M8'),
            ('alert_rules.read', 'Ver las reglas de alerta vigentes.', 'M8'),
            ('alerts.read_assigned', 'Ver alertas de pacientes asignados.', 'M8'),
            ('alerts.read_own', 'Ver alertas propias.', 'M8'),
            ('alerts.acknowledge', 'Reconocer una alerta de un paciente asignado.', 'M8'),
            ('alerts.acknowledge_own', 'Reconocer una alerta propia (primer paso de la cadena de escalamiento).', 'M8'),
            ('alerts.resolve', 'Resolver una alerta de un paciente asignado, con nota.', 'M8'),
            ('alerts.read_summary', 'Ver conteos agregados de alertas de la clínica, sin identificar clínicamente al paciente.', 'M8'),
            ('notifications.read_own', 'Ver y marcar como leídas las notificaciones propias.', 'M8'),
            ('notification_preferences.manage_own', 'Elegir canales y horario de notificaciones propios.', 'M8'),
            ('ai.forecasts.read_assigned', 'Ver pronósticos de glucosa de pacientes asignados.', 'M9'),
            ('ai.forecasts.read_own', 'Ver el pronóstico propio.', 'M9'),
            ('ai.risk.read_assigned', 'Ver el riesgo, los patrones y los factores explicados de pacientes asignados.', 'M9'),
            ('ai.dropout_risk.read', 'Ver el riesgo de abandono (médico: pacientes asignados; administrador: lista sin detalle clínico).', 'M9'),
            ('recommendations.read_assigned', 'Ver las recomendaciones de la IA de pacientes asignados.', 'M9'),
            ('recommendations.decide', 'Aprobar, aprobar con cambios o rechazar una recomendación (motivo obligatorio al modificar o rechazar).', 'M9'),
            ('clinical_summaries.generate', 'Generar y leer el resumen previo a la consulta de un paciente asignado.', 'M9'),
            ('assistant.use', 'Conversar con el asistente educativo.', 'M9'),
            ('ai_evaluations.read', 'Ver la evaluación de la IA frente al respaldo.', 'M9'),
            ('dashboard.read_assigned', 'Ver el dashboard de triage con los pacientes asignados.', 'M10'),
            ('clinical_notes.manage', 'Crear notas clínicas sobre pacientes asignados.', 'M10'),
            ('reports.generate_assigned', 'Generar reportes de un paciente asignado.', 'M10'),
            ('reports.generate_own', 'Generar el reporte propio para compartir con su equipo.', 'M10'),
            ('clinic_kpis.read', 'Ver KPIs agregados de adopción y de la clínica, sin detalle clínico.', 'M10'),
            ('reports.generate_clinic', 'Generar reportes agregados de la clínica.', 'M10'),
            ('messages.use_assigned', 'Leer y responder mensajes de pacientes asignados.', 'M11'),
            ('messages.use_own', 'Escribir al equipo de atención y leer las respuestas.', 'M11'),
            ('follow_ups.manage', 'Programar y cerrar controles de pacientes asignados.', 'M11'),
            ('follow_ups.read_own', 'Ver los controles propios.', 'M11'),
            ('audit.read', 'Consultar la auditoría de la clínica (acciones y accesos), sin contenido clínico.', 'M12'),
            ('audit.read_platform', 'Consultar eventos de seguridad y auditoría de la plataforma.', 'M12'),
            ('consents.manage_own', 'Aceptar, consultar y retirar los consentimientos propios.', 'M12'),
            ('consents.read_status', 'Ver el estado del consentimiento por paciente, sin contenido clínico.', 'M12'),
            ('privacy_requests.create_own', 'Solicitar acceso, rectificación o supresión de los datos propios.', 'M12'),
            ('privacy_requests.manage', 'Tramitar solicitudes de privacidad de la clínica.', 'M12'),
            ('demo_data.manage', 'Crear, reiniciar e importar datos de la clínica demo.', 'M13');
        """
    )

    op.execute(
        """
        INSERT INTO iam.role_permissions (role_id, permission_id)
        SELECT r.id, p.id
          FROM (VALUES
            ('PLATFORM_ADMIN', 'clinics.manage'),
            ('PLATFORM_ADMIN', 'clinic_admins.manage'),
            ('PLATFORM_ADMIN', 'notifications.read_own'),
            ('PLATFORM_ADMIN', 'notification_preferences.manage_own'),
            ('PLATFORM_ADMIN', 'ai_evaluations.read'),
            ('PLATFORM_ADMIN', 'audit.read_platform'),
            ('PLATFORM_ADMIN', 'demo_data.manage'),
            ('CLINIC_ADMIN', 'clinics.read'),
            ('CLINIC_ADMIN', 'sites.manage'),
            ('CLINIC_ADMIN', 'users.manage'),
            ('CLINIC_ADMIN', 'users.read'),
            ('CLINIC_ADMIN', 'patients.import'),
            ('CLINIC_ADMIN', 'import_batches.read'),
            ('CLINIC_ADMIN', 'patients.read_roster'),
            ('CLINIC_ADMIN', 'patients.manage_status'),
            ('CLINIC_ADMIN', 'credentials.issue'),
            ('CLINIC_ADMIN', 'credentials.revoke'),
            ('CLINIC_ADMIN', 'care_team.assign'),
            ('CLINIC_ADMIN', 'care_team.read'),
            ('CLINIC_ADMIN', 'protocol_templates.manage'),
            ('CLINIC_ADMIN', 'protocol_templates.read'),
            ('CLINIC_ADMIN', 'target_profiles.manage'),
            ('CLINIC_ADMIN', 'alert_rules.manage'),
            ('CLINIC_ADMIN', 'alert_rules.read'),
            ('CLINIC_ADMIN', 'alerts.read_summary'),
            ('CLINIC_ADMIN', 'notifications.read_own'),
            ('CLINIC_ADMIN', 'notification_preferences.manage_own'),
            ('CLINIC_ADMIN', 'ai.dropout_risk.read'),
            ('CLINIC_ADMIN', 'ai_evaluations.read'),
            ('CLINIC_ADMIN', 'clinic_kpis.read'),
            ('CLINIC_ADMIN', 'reports.generate_clinic'),
            ('CLINIC_ADMIN', 'audit.read'),
            ('CLINIC_ADMIN', 'consents.read_status'),
            ('CLINIC_ADMIN', 'privacy_requests.manage'),
            ('DOCTOR', 'clinics.read'),
            ('DOCTOR', 'care_team.read'),
            ('DOCTOR', 'patients.read_assigned'),
            ('DOCTOR', 'labs.write_assigned'),
            ('DOCTOR', 'protocol_templates.read'),
            ('DOCTOR', 'treatment_plans.manage'),
            ('DOCTOR', 'target_profiles.assign'),
            ('DOCTOR', 'reminders.manage_assigned'),
            ('DOCTOR', 'metrics.read_assigned'),
            ('DOCTOR', 'alert_rules.read'),
            ('DOCTOR', 'alerts.read_assigned'),
            ('DOCTOR', 'alerts.acknowledge'),
            ('DOCTOR', 'alerts.resolve'),
            ('DOCTOR', 'notifications.read_own'),
            ('DOCTOR', 'notification_preferences.manage_own'),
            ('DOCTOR', 'ai.forecasts.read_assigned'),
            ('DOCTOR', 'ai.risk.read_assigned'),
            ('DOCTOR', 'ai.dropout_risk.read'),
            ('DOCTOR', 'recommendations.read_assigned'),
            ('DOCTOR', 'recommendations.decide'),
            ('DOCTOR', 'clinical_summaries.generate'),
            ('DOCTOR', 'dashboard.read_assigned'),
            ('DOCTOR', 'clinical_notes.manage'),
            ('DOCTOR', 'reports.generate_assigned'),
            ('DOCTOR', 'messages.use_assigned'),
            ('DOCTOR', 'follow_ups.manage'),
            ('PATIENT', 'patients.read_own'),
            ('PATIENT', 'glucose.write_own'),
            ('PATIENT', 'glucose.read_own'),
            ('PATIENT', 'records.write_own'),
            ('PATIENT', 'records.read_own'),
            ('PATIENT', 'records.correct_own'),
            ('PATIENT', 'labs.write_own'),
            ('PATIENT', 'treatment_plans.read_own'),
            ('PATIENT', 'reminders.manage_own'),
            ('PATIENT', 'metrics.read_own'),
            ('PATIENT', 'alerts.read_own'),
            ('PATIENT', 'alerts.acknowledge_own'),
            ('PATIENT', 'notifications.read_own'),
            ('PATIENT', 'notification_preferences.manage_own'),
            ('PATIENT', 'ai.forecasts.read_own'),
            ('PATIENT', 'assistant.use'),
            ('PATIENT', 'reports.generate_own'),
            ('PATIENT', 'messages.use_own'),
            ('PATIENT', 'follow_ups.read_own'),
            ('PATIENT', 'consents.manage_own'),
            ('PATIENT', 'privacy_requests.create_own')
               ) AS g (role_code, permission_code)
          JOIN iam.roles AS r ON r.code = g.role_code
          JOIN iam.permissions AS p ON p.code = g.permission_code;

        DO $check$
        BEGIN
            IF (SELECT count(*) FROM iam.role_permissions) <> 81 THEN
                RAISE EXCEPTION 'role_permissions seed is incomplete';
            END IF;
        END
        $check$;
        """
    )


def downgrade() -> None:
    """Delete the seeded catalogs (fails if a membership still uses a role)."""
    op.execute(
        """
        DELETE FROM iam.role_permissions;
        DELETE FROM iam.permissions;
        DELETE FROM iam.roles;
        """
    )

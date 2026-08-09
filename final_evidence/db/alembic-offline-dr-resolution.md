# Alembic offline and DR resolution

Verdict: `SCHEMA_PROVEN`.

- Single head: `150_multimodule_hardening`.
- Offline `alembic upgrade head --sql`: exit `0`.
- Fresh isolated database upgrade from baseline through head: exit `0`.
- Restored schema head: `150_multimodule_hardening`.
- Final staging API startup: `alembic upgrade head OK`.
- Critical schema validation: all `32` required columns present.
- The API and dedicated worker started successfully after the migration gate.

The proof used disposable databases created for this remediation. No production migration was executed.

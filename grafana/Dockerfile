# Custom Grafana image for Scalpyn — bakes in the Trading Engine dashboard
# and the provisioning files (datasources + dashboard provider) so the
# Cloud Run service comes up fully configured on first cold start.
#
# Build context expected to be the repo root so we can COPY both ./grafana
# and ./docs/grafana into the image:
#   docker build -t scalpyn-grafana -f grafana/Dockerfile .
#
# The image is intentionally thin — Grafana itself stays on the upstream
# OSS image; we only layer in static config + the dashboard JSON.

FROM grafana/grafana-oss:11.3.0

# Provisioning files (datasources + dashboard provider).
# Filenames are arbitrary; Grafana scans the directories at startup.
COPY grafana/provisioning/datasources/datasources.yaml \
     /etc/grafana/provisioning/datasources/datasources.yaml
COPY grafana/provisioning/dashboards/dashboards.yaml \
     /etc/grafana/provisioning/dashboards/dashboards.yaml

# The dashboard JSON itself — referenced by the dashboard provider above.
# Lives under docs/grafana/ in the repo so it stays the single source of
# truth for both manual imports and the baked image.
COPY docs/grafana/scalpyn-trading-engine.json \
     /etc/grafana/provisioning/dashboards/scalpyn-trading-engine.json

# Optional: the four unified-alerting rules. Mirrors the in-dashboard
# embedded alerts; importing both is idempotent (provisioning wins on
# conflict). Datasource UID placeholders inside the YAML are replaced at
# build time via cloudbuild substitutions when needed; here we ship the
# file as-is and the operator can drop a rendered copy via env var
# overrides if they want provisioning-driven alerts.
COPY docs/grafana/alert-rules.yaml \
     /etc/grafana/provisioning/alerting/scalpyn.yaml.template

# Grafana listens on 3000 by default; Cloud Run will route traffic here.
EXPOSE 3000

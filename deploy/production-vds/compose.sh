#!/bin/bash
# Runtime Compose wrapper for the PlantDiseases production stack on this VDS.
# RELEASE is the exact checkout whose SHA was built and recorded.
#   compose.sh build-images   builds both images from RELEASE (tags, not digests)
#   compose.sh <compose args> everything else, with the recorded digests
set -euo pipefail
export RELEASE=${RELEASE:-/opt/pd-account-prod/releases/305f1ad87fd0}
SHA=$(basename "$RELEASE")
cd "$RELEASE/deploy/account-beta-test"
base=(docker compose --project-directory "$RELEASE/deploy/account-beta-test"
      --env-file /etc/pd-account-prod/account-prod.env
      -f /opt/pd-account-prod/deploy/compose.production.yml
      -f /opt/pd-account-prod/deploy/compose.operator.yml)
if [ "${1:-}" = "build-images" ]; then
  export PROD_ACCOUNT_IMAGE="pdis-account-api:prod-$SHA" PROD_APP_IMAGE="pdis-app-api:prod-$SHA"
  exec "${base[@]}" -f /opt/pd-account-prod/deploy/compose.build.yml build prod-account-api prod-app-api
fi
exec "${base[@]}" "$@"

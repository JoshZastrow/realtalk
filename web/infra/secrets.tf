# Anthropic key: created outside Terraform (secret `conle-config` in
# GCP project `conle-dev`, already populated with the API key). We only
# reference it and grant the runtime SA read access.
#
# Signing key: Terraform-managed because it's a symmetric key only the
# service needs. We use a random_password and push it into Secret
# Manager; the value lives in HCP state (encrypted at rest) and is never
# printed by plan/apply output.

data "google_secret_manager_secret" "anthropic_api_key" {
  project   = var.gcp_project_id
  secret_id = var.anthropic_secret_id

  depends_on = [google_project_service.required]
}

resource "random_password" "session_signing_key" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "session_token_signing_key" {
  project   = var.gcp_project_id
  secret_id = "session-token-signing-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "session_token_signing_key" {
  secret      = google_secret_manager_secret.session_token_signing_key.id
  secret_data = random_password.session_signing_key.result
}

resource "google_secret_manager_secret_iam_member" "anthropic_accessor" {
  project   = var.gcp_project_id
  secret_id = data.google_secret_manager_secret.anthropic_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "signing_key_accessor" {
  project   = var.gcp_project_id
  secret_id = google_secret_manager_secret.session_token_signing_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

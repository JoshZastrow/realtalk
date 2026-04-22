# Two services. realtalk-ws is provisioned first so realtalk-api can
# reference its URL for the wss:// link it returns to the browser.

resource "google_cloud_run_v2_service" "ws" {
  project  = var.gcp_project_id
  location = var.gcp_region
  name     = var.service_name_ws
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account       = google_service_account.runtime.email
    max_instance_request_concurrency = 1
    timeout               = "3600s"

    scaling {
      min_instance_count = 1
      max_instance_count = var.max_sessions
    }

    containers {
      image = local.image_ws

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "REALTALK_ALLOWED_ORIGINS"
        value = join(",", var.allowed_origins)
      }
      env {
        name  = "REALTALK_MAX_SESSIONS"
        value = tostring(var.max_sessions)
      }
      env {
        name  = "REALTALK_COMMAND"
        value = "realtalk --no-color"
      }
      env {
        name = "REALTALK_SIGNING_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.session_token_signing_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.anthropic_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.gcp_project_id
      }

      startup_probe {
        http_get {
          path = "/_health"
        }
        timeout_seconds   = 5
        period_seconds    = 5
        failure_threshold = 10
      }
    }
  }

  depends_on = [
    google_artifact_registry_repository.realtalk,
    google_firestore_database.default,
    google_project_iam_member.runtime_firestore,
    google_secret_manager_secret_iam_member.anthropic_accessor,
    google_secret_manager_secret_iam_member.signing_key_accessor,
    google_secret_manager_secret_version.session_token_signing_key,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "ws_public" {
  project  = var.gcp_project_id
  location = var.gcp_region
  name     = google_cloud_run_v2_service.ws.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service" "api" {
  project  = var.gcp_project_id
  location = var.gcp_region
  name     = var.service_name_api
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account       = google_service_account.runtime.email
    max_instance_request_concurrency = 80
    timeout               = "30s"

    scaling {
      min_instance_count = 1
      max_instance_count = 3
    }

    containers {
      image = local.image_api

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      env {
        name  = "REALTALK_ALLOWED_ORIGINS"
        value = join(",", var.allowed_origins)
      }
      env {
        name  = "REALTALK_MAX_SESSIONS"
        value = tostring(var.max_sessions)
      }
      env {
        name  = "REALTALK_RATE_LIMIT_PER_HOUR"
        value = tostring(var.rate_limit_per_hour)
      }
      env {
        name  = "REALTALK_WS_BASE_URL"
        value = var.ws_custom_domain != "" ? "wss://${var.ws_custom_domain}" : replace(google_cloud_run_v2_service.ws.uri, "https://", "wss://")
      }
      env {
        name = "REALTALK_SIGNING_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.session_token_signing_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.gcp_project_id
      }

      startup_probe {
        http_get {
          path = "/_health"
        }
        timeout_seconds   = 5
        period_seconds    = 5
        failure_threshold = 10
      }
    }
  }

  depends_on = [
    google_cloud_run_v2_service.ws,
    google_firestore_database.default,
    google_project_iam_member.runtime_firestore,
    google_secret_manager_secret_iam_member.signing_key_accessor,
    google_secret_manager_secret_version.session_token_signing_key,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  project  = var.gcp_project_id
  location = var.gcp_region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

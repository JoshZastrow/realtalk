resource "google_cloud_run_domain_mapping" "api" {
  count    = var.api_custom_domain == "" ? 0 : 1
  project  = var.gcp_project_id
  location = var.gcp_region
  name     = var.api_custom_domain

  metadata {
    namespace = var.gcp_project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.api.name
  }
}

resource "google_cloud_run_domain_mapping" "ws" {
  count    = var.ws_custom_domain == "" ? 0 : 1
  project  = var.gcp_project_id
  location = var.gcp_region
  name     = var.ws_custom_domain

  metadata {
    namespace = var.gcp_project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.ws.name
  }
}

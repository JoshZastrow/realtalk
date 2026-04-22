resource "google_artifact_registry_repository" "realtalk" {
  provider = google

  project       = var.gcp_project_id
  location      = var.gcp_region
  repository_id = "realtalk"
  description   = "Container images for realtalk-api and realtalk-ws"
  format        = "DOCKER"

  depends_on = [google_project_service.required]
}

locals {
  image_host = "${var.gcp_region}-docker.pkg.dev"
  image_api  = "${local.image_host}/${var.gcp_project_id}/realtalk/${var.service_name_api}:${var.image_tag}"
  image_ws   = "${local.image_host}/${var.gcp_project_id}/realtalk/${var.service_name_ws}:${var.image_tag}"
}

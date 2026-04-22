resource "google_project_service" "required" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "firestore.googleapis.com",
    "cloudbuild.googleapis.com",
    "logging.googleapis.com",
  ])
  project            = var.gcp_project_id
  service            = each.value
  disable_on_destroy = false
}

locals {
  ws_base_url = (
    var.ws_custom_domain != ""
    ? "wss://${var.ws_custom_domain}"
    : "__PLACEHOLDER__"  # filled in below from cloud run uri
  )
}

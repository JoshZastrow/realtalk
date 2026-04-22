output "api_url" {
  description = "Public URL for the realtalk-api (token mint)."
  value       = google_cloud_run_v2_service.api.uri
}

output "ws_url" {
  description = "Public URL for the realtalk-ws (WebSocket host). Rewrite to wss:// on the client."
  value       = google_cloud_run_v2_service.ws.uri
}

output "api_image" {
  description = "Artifact Registry image reference for realtalk-api."
  value       = local.image_api
}

output "ws_image" {
  description = "Artifact Registry image reference for realtalk-ws."
  value       = local.image_ws
}

output "artifact_registry_host" {
  description = "Artifact Registry host for docker push."
  value       = local.image_host
}

output "artifact_registry_repo" {
  description = "Artifact Registry repository path."
  value       = "${local.image_host}/${var.gcp_project_id}/${google_artifact_registry_repository.realtalk.repository_id}"
}

output "runtime_service_account" {
  description = "Email of the Cloud Run runtime service account."
  value       = google_service_account.runtime.email
}

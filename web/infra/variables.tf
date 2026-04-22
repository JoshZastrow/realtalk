variable "gcp_project_id" {
  description = "GCP project ID (must already exist with billing linked)."
  type        = string
}

variable "gcp_region" {
  description = "GCP region for Cloud Run, Artifact Registry, Firestore."
  type        = string
  default     = "us-central1"
}

variable "service_name_api" {
  description = "Cloud Run service name for the stateless token-mint service."
  type        = string
  default     = "realtalk-api"
}

variable "service_name_ws" {
  description = "Cloud Run service name for the PTY-hosting WebSocket service."
  type        = string
  default     = "realtalk-ws"
}

variable "image_tag" {
  description = "Tag applied to both images (typically the git short SHA)."
  type        = string
  default     = "latest"
}

variable "allowed_origins" {
  description = "Browser origins allowed to hit the api and open WebSockets."
  type        = list(string)
  default     = ["https://conle.ai"]
}

variable "max_sessions" {
  description = "Hard ceiling on concurrent PTY sessions (realtalk-ws max instances)."
  type        = number
  default     = 10
}

variable "rate_limit_per_hour" {
  description = "Per-IP cap at the realtalk-api /session endpoint."
  type        = number
  default     = 5
}

variable "ws_custom_domain" {
  description = "Optional custom domain for realtalk-ws (e.g. ws.realtalk.conle.ai). Empty = skip mapping."
  type        = string
  default     = ""
}

variable "api_custom_domain" {
  description = "Optional custom domain for realtalk-api (e.g. api.realtalk.conle.ai). Empty = skip mapping."
  type        = string
  default     = ""
}

variable "anthropic_secret_id" {
  description = "Secret Manager secret ID holding the Anthropic API key."
  type        = string
  default     = "conle-config"
}

# Firestore database in Native mode. Holds the capacity counter
# (capacity/global doc) used by both services. Phase 4 extends it with
# kill-switch and daily-spend-counter collections.

resource "google_firestore_database" "default" {
  project         = var.gcp_project_id
  name            = "(default)"
  location_id     = var.gcp_region
  type            = "FIRESTORE_NATIVE"
  deletion_policy = "DELETE"

  depends_on = [google_project_service.required]
}

terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  cloud {
    organization = "conle"
    workspaces {
      name = "realtalk-web"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

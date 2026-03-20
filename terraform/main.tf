<<<<<<< HEAD
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  required_services = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = var.artifact_registry_repository_id
  description   = "Docker images for the AI Accounting Agent API"
  format        = "DOCKER"

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = var.gemini_api_key_secret_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "endpoint_api_key" {
  count     = var.enable_endpoint_api_key ? 1 : 0
  secret_id = var.endpoint_api_key_secret_id

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "gemini_api_key_accessor" {
  secret_id = google_secret_manager_secret.gemini_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.cloud_run_service_account_email}"
}

resource "google_secret_manager_secret_iam_member" "endpoint_api_key_accessor" {
  count     = var.enable_endpoint_api_key ? 1 : 0
  secret_id = google_secret_manager_secret.endpoint_api_key[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.cloud_run_service_account_email}"
}

resource "google_cloud_run_v2_service" "api" {
  name     = var.cloud_run_service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = var.cloud_run_service_account_email
    timeout         = "300s"

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_api_key.secret_id
            version = "latest"
          }
        }
      }

      dynamic "env" {
        for_each = var.enable_endpoint_api_key ? [google_secret_manager_secret.endpoint_api_key[0]] : []
        content {
          name = "AI_ACCOUNTING_AGENT_API_KEY"
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.gemini_api_key_accessor,
    google_secret_manager_secret_iam_member.endpoint_api_key_accessor,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.images.id
}

output "cloud_run_service_uri" {
  value = google_cloud_run_v2_service.api.uri
}

output "gemini_api_key_secret_name" {
  value = google_secret_manager_secret.gemini_api_key.secret_id
}

output "endpoint_api_key_secret_name" {
  value = var.enable_endpoint_api_key ? google_secret_manager_secret.endpoint_api_key[0].secret_id : null
}

output "next_steps" {
  value = [
    "Build and push the container image defined by var.container_image.",
    "Add a secret version to ${google_secret_manager_secret.gemini_api_key.secret_id} with the Gemini API key.",
    var.enable_endpoint_api_key ? "Add a secret version to ${google_secret_manager_secret.endpoint_api_key[0].secret_id} with the endpoint bearer token." : "Endpoint bearer auth is disabled.",
    "Call the public Cloud Run URL with POST /solve and Authorization: Bearer <token> when endpoint auth is enabled.",
  ]
=======
# Configure the Azure provider
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.65.0"
    }
  }
  backend "azurerm" {
    resource_group_name  = "" # fill resource group name of storage account for tfstate
    storage_account_name = "" # fill storage account name
    container_name       = "" # fill container name
    key                  = "" # Fill like "NAME_OF_PROJECT.tfstate"
  }
}

provider "azurerm" {
  features {}
>>>>>>> 193efcfe0d04587a93521c728b75fd5ac3b98077
}

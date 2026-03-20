<<<<<<< HEAD
variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "region" {
  description = "Google Cloud region for Cloud Run, Artifact Registry, and Secret Manager."
  type        = string
  default     = "europe-west4"
}

variable "artifact_registry_repository_id" {
  description = "Artifact Registry Docker repository identifier."
  type        = string
  default     = "ai-accounting-agent"
}

variable "cloud_run_service_name" {
  description = "Cloud Run service name."
  type        = string
  default     = "ai-accounting-agent"
}

variable "cloud_run_service_account_email" {
  description = "Service account email used by the Cloud Run service."
  type        = string
}

variable "container_image" {
  description = "Full container image path pushed to Artifact Registry."
  type        = string
}

variable "gemini_api_key_secret_id" {
  description = "Secret Manager secret ID used for GEMINI_API_KEY."
  type        = string
  default     = "ai-accounting-agent-gemini-api-key"
}

variable "endpoint_api_key_secret_id" {
  description = "Secret Manager secret ID used for the optional AI_ACCOUNTING_AGENT_API_KEY."
  type        = string
  default     = "ai-accounting-agent-endpoint-api-key"
}

variable "enable_endpoint_api_key" {
  description = "When true, inject AI_ACCOUNTING_AGENT_API_KEY into Cloud Run from Secret Manager."
  type        = bool
  default     = false
}
=======
variable "RG_NAME" {
  description = "Resource group"
  type        = string
}

variable "LOC" {
  description = "Location of resources"
  type        = string
}
>>>>>>> 193efcfe0d04587a93521c728b75fd5ac3b98077

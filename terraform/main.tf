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
}

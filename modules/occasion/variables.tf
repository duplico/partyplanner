variable "domain" {
  description = "Fully qualified domain for the occasion site, e.g. bbq-2026.events.example.com."
  type        = string
}

variable "zone_id" {
  description = "Route53 hosted zone ID that contains the domain."
  type        = string
}

variable "api_throttle_rate" {
  description = "Steady-state API requests per second."
  type        = number
  default     = 5
}

variable "api_throttle_burst" {
  description = "API request burst limit."
  type        = number
  default     = 10
}

variable "lambda_reserved_concurrency" {
  description = "Reserved concurrent executions for the RSVP Lambda (cost guardrail)."
  type        = number
  default     = 5
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}

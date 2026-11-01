variable "name" {
  type    = string
  default = "fraud-scoring"
}
variable "region" {
  type    = string
  default = "us-west-2"
}
variable "image_tag" {
  type    = string
  default = "1.0.0"
}
variable "cpu" {
  type    = number
  default = 512 # 0.5 vCPU
}
variable "memory" {
  type    = number
  default = 1024
}
variable "desired_count" {
  type    = number
  default = 2
}
variable "max_count" {
  type    = number
  default = 6
}
variable "cpu_target" {
  description = "Target average CPU % for ECS service autoscaling"
  type        = number
  default     = 60
}

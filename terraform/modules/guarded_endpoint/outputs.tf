output "url" {
  description = "OpenAI-compatible chat-completions route."
  value       = "${aws_api_gateway_stage.demo.invoke_url}/v1/chat/completions"
}

output "api_key" {
  description = "Demo API key. Treat as a secret."
  value       = aws_api_gateway_api_key.this.value
  sensitive   = true
}

output "guardrail_id" {
  value = aws_bedrock_guardrail.this.guardrail_id
}

output "guardrail_version" {
  value = aws_bedrock_guardrail_version.this.version
}
